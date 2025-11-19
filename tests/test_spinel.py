from __future__ import annotations

import asyncio
import logging
import sys
from unittest.mock import MagicMock, call

import pytest

from universal_silabs_flasher.spinel import (
    CommandID,
    HDLCLiteFrame,
    SpinelFrame,
    SpinelHeader,
    SpinelProtocol,
)
from universal_silabs_flasher.spinel_types import PackedUInt21, PropertyID

if sys.version_info[:2] < (3, 11):
    from async_timeout import timeout as asyncio_timeout  # pragma: no cover
else:
    from asyncio import timeout as asyncio_timeout  # pragma: no cover

from universal_silabs_flasher.common import crc16_kermit
import universal_silabs_flasher.spinel as spinel

from .common import PairedTransport

_LOGGER = logging.getLogger(__name__)


@pytest.mark.parametrize(
    "data, crc",
    [
        (b"", 0x0000),
        (b"foobar", 0x147B),
        (b"\xfa\x9b\x51\xb9\xf2\x53\xe3\xbd", 0x6782),
    ],
)
def test_hdlc_lite_crc(data, crc):
    assert crc16_kermit(data) == crc


@pytest.mark.parametrize(
    "encoded, decoded",
    [
        (bytes.fromhex("7e810243d3d37e"), bytes.fromhex("810243")),
        (bytes.fromhex("7e8103367d5e7d5d6af97e"), bytes.fromhex("8103367e7d")),
        (bytes.fromhex("7e810365010b287e"), bytes.fromhex("81036501")),
        (bytes.fromhex("7e8103862a01547d5e7e"), bytes.fromhex("8103862a01")),
        (
            bytes.fromhex(
                "7e8106024f50454e5448524541442f366666316163302d64697274793b204546523332"
                "3b2044656320323320323032322031383a30383a303000fa8c7e"
            ),
            bytes.fromhex(
                "8106024f50454e5448524541442f366666316163302d64697274793b2045465233323b"
                "2044656320323320323032322031383a30383a303000"
            ),
        ),
    ],
)
def test_hdlc_lite_encoding_decoding(encoded, decoded):
    assert spinel.HDLCLiteFrame(data=decoded).serialize() == encoded
    assert spinel.HDLCLiteFrame.from_bytes(encoded).data == decoded


@pytest.mark.parametrize(
    "encoded, decoded",
    [
        (
            bytes.fromhex(
                "8106024f50454e5448524541442f366666316163302d64697274793b2045465233323b"
                "2044656320323320323032322031383a30383a303000"
            ),
            spinel.SpinelFrame(
                header=spinel.SpinelHeader(
                    transaction_id=1,
                    network_link_id=0,
                    flag=0b10,
                ),
                command_id=spinel.CommandID.PROP_VALUE_IS,
                data=b"\x02OPENTHREAD/6ff1ac0-dirty; EFR32; Dec 23 2022 18:08:00\x00",
            ),
        ),
    ],
)
def test_spinel_parsing(encoded, decoded):
    assert spinel.SpinelFrame.from_bytes(encoded) == decoded
    assert decoded.serialize() == encoded


class Conversation(asyncio.Protocol):
    """A helper to script a conversation with a protocol."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.transport: PairedTransport | None = None
        self._reader = asyncio.StreamReader(loop=loop)

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, PairedTransport)
        self.transport = transport

    def data_received(self, data: bytes) -> None:
        self._reader.feed_data(data)

    def connection_lost(self, exc: Exception | None) -> None:
        self._reader.feed_eof()

    async def send(self, data: bytes) -> None:
        assert self.transport is not None
        self.transport.write(data)


async def create_spinel_test_pair() -> tuple[SpinelProtocol, Conversation]:
    """Creates a connected pair of a client protocol and a conversation helper."""
    loop = asyncio.get_running_loop()
    client = SpinelProtocol()
    conversation = Conversation(loop)

    client_transport = PairedTransport(conversation, loop)
    server_transport = PairedTransport(client, loop)

    client.connection_made(client_transport)
    conversation.connection_made(server_transport)

    return client, conversation


async def test_ignore_duplicate_response(caplog) -> None:
    """Test that duplicate responses are ignored."""
    client, conversation = await create_spinel_test_pair()

    # Send a command and get the future
    fut = asyncio.create_task(client.send_command(CommandID.PROP_VALUE_GET, b"\x01"))
    # Let the command be sent
    await asyncio.sleep(0.01)

    assert len(client._pending_frames) == 1
    tid = list(client._pending_frames.keys())[0]

    # Receive the first response
    response1 = SpinelFrame(
        header=SpinelHeader(transaction_id=tid, network_link_id=0, flag=0b10),
        command_id=CommandID.PROP_VALUE_IS,
        data=b"\x01test",
    )
    await conversation.send(HDLCLiteFrame(data=response1.serialize()).serialize())

    with caplog.at_level(logging.DEBUG):
        # Receive the duplicate response immediately
        response2 = SpinelFrame(
            header=SpinelHeader(transaction_id=tid, network_link_id=0, flag=0b10),
            command_id=CommandID.PROP_VALUE_IS,
            data=b"\x01test2",
        )

        await conversation.send(HDLCLiteFrame(data=response2.serialize()).serialize())
        await asyncio.sleep(0.01)

    # The future should be resolved with the first response
    async with asyncio_timeout(1):
        result = await fut

    assert result == response1
    assert f"Ignoring duplicate response for TID {tid}" in caplog.text


async def test_ignore_tid_zero_response(caplog) -> None:
    """Test that responses with TID 0 are ignored if no future is pending."""
    client, conversation = await create_spinel_test_pair()

    caplog.set_level(logging.DEBUG)
    caplog.clear()

    # We don't have a pending future for TID 0
    assert 0 not in client._pending_frames

    # Receive a response with TID 0
    response = SpinelFrame(
        header=SpinelHeader(transaction_id=0, network_link_id=0, flag=0b10),
        command_id=CommandID.PROP_VALUE_IS,
        data=b"\x01test",
    )
    await conversation.send(HDLCLiteFrame(data=response.serialize()).serialize())
    await asyncio.sleep(0.01)

    # Nothing should have happened
    for record in caplog.records:
        assert record.levelno < logging.WARNING


async def test_property_listener() -> None:
    """Test the property listener mechanism."""
    client, conversation = await create_spinel_test_pair()
    listener = MagicMock()
    prop_id = PropertyID.STREAM_DEBUG
    prop_data = b"hello world"

    client.add_property_listener(prop_id, listener)

    # Send a PROP_VALUE_IS frame
    frame_data = PackedUInt21.serialize(prop_id) + prop_data
    response = SpinelFrame(
        header=SpinelHeader(transaction_id=0, network_link_id=0, flag=0b10),
        command_id=CommandID.PROP_VALUE_IS,
        data=frame_data,
    )
    await conversation.send(HDLCLiteFrame(data=response.serialize()).serialize())
    await asyncio.sleep(0.01)

    listener.mock_calls == [call(prop_data)]

    # Test removing the listener
    listener.reset_mock()
    client.remove_property_listener(prop_id, listener)
    await conversation.send(HDLCLiteFrame(data=response.serialize()).serialize())
    await asyncio.sleep(0.01)

    listener.mock_calls == []


async def test_iter_property_changes() -> None:
    """Test the async iterator for property changes."""
    client, conversation = await create_spinel_test_pair()
    prop_id = PropertyID.STREAM_DEBUG
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    async def _iterator_task():
        async for value in client.iter_property_changes(prop_id):
            await queue.put(value)

    task = asyncio.create_task(_iterator_task())

    # Send a few PROP_VALUE_IS frames
    for i in range(5):
        response = SpinelFrame(
            header=SpinelHeader(transaction_id=0, network_link_id=0, flag=0b10),
            command_id=CommandID.PROP_VALUE_IS,
            data=PackedUInt21.serialize(prop_id) + f"test {i}".encode("ascii"),
        )
        await conversation.send(HDLCLiteFrame(data=response.serialize()).serialize())

    async with asyncio_timeout(1):
        results = [await queue.get() for _ in range(5)]

    assert results == [f"test {i}".encode("ascii") for i in range(5)]

    task.cancel()


async def test_send_frame_tid_zero_raises() -> None:
    """Test that send_frame with tid=0 and wait_response=True raises ValueError."""
    client, _ = await create_spinel_test_pair()
    frame = SpinelFrame(
        header=SpinelHeader(transaction_id=0, network_link_id=0, flag=0b10),
        command_id=CommandID.NOOP,
        data=b"",
    )

    with pytest.raises(ValueError, match="Cannot wait for response on TID=0 frames"):
        await client.send_frame(frame, tid=0, wait_response=True)

    # Should not raise when wait_response is False
    await client.send_frame(frame, tid=0, wait_response=False)


@pytest.mark.parametrize(
    ("value", "serialized"),
    [
        (0, b"\x00"),
        (42, b"\x2a"),
        (200, b"\xc8\x01"),
        (1337, b"\xb9\x0a"),
        (40000, b"\xc0\xb8\x02"),
        (2097151, b"\xff\xff\x7f"),
    ],
)
def test_packed_uint21_serialization(value, serialized):
    """Test PackedUInt21 serialization."""

    assert PackedUInt21(value).serialize() == serialized

    deserialized, remainder = PackedUInt21.deserialize(serialized + b"remainder")
    assert deserialized == value
    assert remainder == b"remainder"


def test_packed_uint21_deserialization_failure():
    """Test PackedUInt21 deserialization failure on oversized input."""

    with pytest.raises(ValueError):
        PackedUInt21.deserialize(b"\x80\x80\x80\x80\x00")
