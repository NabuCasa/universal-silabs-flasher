from __future__ import annotations

import asyncio
import logging
from unittest.mock import Mock, patch

import pytest

from universal_silabs_flasher.common import BufferTooShort, Version
from universal_silabs_flasher.zwave import (
    ACK,
    FunctionID,
    MessageType,
    ZWaveFrame,
    ZWaveProtocol,
)

from .common import PairedTransport

ENTER_BOOTLOADER_REQUEST = bytes.fromhex("01030027db")
GET_CAPABILITIES_REQUEST = bytes.fromhex("01030007fb")
GET_CAPABILITIES_RESPONSE = ZWaveFrame(
    type=MessageType.RESPONSE,
    function_id=FunctionID.SERIAL_API_GET_CAPABILITIES,
    data=bytes.fromhex(
        "0102046600010001f6873e88cf2bc05fe3d7fde0970f008000808680ba0d00f000002e7fe0800000"
    ),
)


def test_frame_serialize() -> None:
    frame = ZWaveFrame(
        type=MessageType.REQUEST,
        function_id=FunctionID.SERIAL_API_GET_CAPABILITIES,
        data=b"",
    )
    assert frame.serialize() == GET_CAPABILITIES_REQUEST

    frame = ZWaveFrame(
        type=MessageType.REQUEST,
        function_id=FunctionID.SERIAL_API_ENTER_BOOTLOADER,
        data=b"",
    )
    assert frame.serialize() == ENTER_BOOTLOADER_REQUEST


def test_frame_deserialize_roundtrip() -> None:
    for frame in (
        ZWaveFrame(
            type=MessageType.REQUEST,
            function_id=FunctionID.SERIAL_API_GET_CAPABILITIES,
            data=b"",
        ),
        GET_CAPABILITIES_RESPONSE,
    ):
        parsed, remainder = ZWaveFrame.deserialize(frame.serialize())
        assert parsed == frame
        assert remainder == b""


def test_frame_deserialize_returns_remainder() -> None:
    raw = GET_CAPABILITIES_RESPONSE.serialize() + b"\xab\xcd"
    parsed, remainder = ZWaveFrame.deserialize(raw)
    assert parsed == GET_CAPABILITIES_RESPONSE
    assert remainder == b"\xab\xcd"


def test_frame_deserialize_too_short() -> None:
    with pytest.raises(BufferTooShort):
        ZWaveFrame.deserialize(b"\x01")

    with pytest.raises(BufferTooShort):
        ZWaveFrame.deserialize(b"\x01\x10")  # claims 16 bytes but body is missing


def test_frame_deserialize_bad_checksum() -> None:
    raw = bytearray(GET_CAPABILITIES_REQUEST)
    raw[-1] ^= 0xFF  # corrupt checksum
    with pytest.raises(ValueError, match="Checksum mismatch"):
        ZWaveFrame.deserialize(bytes(raw))


def test_frame_deserialize_wrong_sof() -> None:
    with pytest.raises(ValueError, match="Expected SOF"):
        ZWaveFrame.deserialize(b"\x00\x03\x00\x07\xfb")


@pytest.mark.parametrize(
    "chunks",
    [
        [GET_CAPABILITIES_REQUEST],
        [bytes([b]) for b in GET_CAPABILITIES_REQUEST],
    ],
)
def test_protocol_data_received(chunks: list[bytes]) -> None:
    protocol = ZWaveProtocol()
    protocol.connection_made(Mock())

    with patch.object(protocol, "frame_received") as mock:
        for chunk in chunks:
            protocol.data_received(chunk)

    assert mock.call_count == 1
    parsed_frame = mock.call_args[0][0]
    assert parsed_frame == ZWaveFrame(
        type=MessageType.REQUEST,
        function_id=FunctionID.SERIAL_API_GET_CAPABILITIES,
        data=b"",
    )


def test_protocol_sends_ack_on_valid_frame() -> None:
    loop = asyncio.new_event_loop()
    protocol = ZWaveProtocol()
    received = bytearray()

    class CapturingTransport(asyncio.Transport):
        def write(self, data):
            received.extend(data)

        def is_closing(self):
            return False

    protocol.connection_made(CapturingTransport())
    protocol.data_received(GET_CAPABILITIES_REQUEST)

    assert received == bytes([ACK])
    loop.close()


def test_protocol_skips_ack_nak_can_and_junk() -> None:
    protocol = ZWaveProtocol()
    protocol.connection_made(Mock())

    with patch.object(protocol, "frame_received") as mock:
        # ACK + NAK + CAN + junk + valid frame
        protocol.data_received(
            bytes([0x06, 0x15, 0x18, 0xAA]) + GET_CAPABILITIES_REQUEST
        )

    assert mock.call_count == 1


def test_protocol_bad_checksum_recovers() -> None:
    protocol = ZWaveProtocol()
    protocol.connection_made(Mock())

    bad = bytearray(GET_CAPABILITIES_REQUEST)
    bad[-1] ^= 0xFF

    with patch.object(protocol, "frame_received") as mock:
        protocol.data_received(bytes(bad) + GET_CAPABILITIES_REQUEST)

    assert mock.call_count == 1


class EchoSide(asyncio.Protocol):
    def __init__(self) -> None:
        self.transport: asyncio.Transport | None = None
        self.received = bytearray()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def data_received(self, data: bytes) -> None:
        self.received.extend(data)


async def _make_pair() -> tuple[ZWaveProtocol, EchoSide]:
    loop = asyncio.get_running_loop()

    client = ZWaveProtocol()
    server = EchoSide()

    client.connection_made(PairedTransport(server, loop))
    server.connection_made(PairedTransport(client, loop))

    return client, server


async def test_probe() -> None:
    client, server = await _make_pair()

    async def _respond():
        await asyncio.sleep(0.01)
        assert server.transport is not None
        server.transport.write(GET_CAPABILITIES_RESPONSE.serialize())

    asyncio.create_task(_respond())
    version = await client.probe()
    assert version == Version("1.2")


async def test_probe_timeout() -> None:
    client, _ = await _make_pair()

    with pytest.raises(asyncio.TimeoutError):
        await client.send_command(
            FunctionID.SERIAL_API_GET_CAPABILITIES, timeout=0.05, retries=0
        )


async def test_enter_bootloader() -> None:
    client, server = await _make_pair()
    await client.enter_bootloader()
    await asyncio.sleep(0.01)
    assert ENTER_BOOTLOADER_REQUEST in bytes(server.received)


async def test_unsolicited_response_ignored() -> None:
    client, server = await _make_pair()

    # Send an unsolicited response with no pending future — should not raise
    assert server.transport is not None
    server.transport.write(GET_CAPABILITIES_RESPONSE.serialize())
    await asyncio.sleep(0.01)
    assert not client._pending_frames


async def test_duplicate_response_ignored(caplog: pytest.LogCaptureFixture) -> None:
    client, server = await _make_pair()

    async def _respond_twice():
        await asyncio.sleep(0.01)
        assert server.transport is not None
        server.transport.write(GET_CAPABILITIES_RESPONSE.serialize())
        server.transport.write(GET_CAPABILITIES_RESPONSE.serialize())

    asyncio.create_task(_respond_twice())

    with caplog.at_level(logging.DEBUG):
        result = await client.send_command(FunctionID.SERIAL_API_GET_CAPABILITIES)

    assert result == GET_CAPABILITIES_RESPONSE
    assert "duplicate" in caplog.text
