from __future__ import annotations

import asyncio
import logging
import pathlib
import sys

import pytest

from universal_silabs_flasher.common import crc16_ccitt, pad_to_multiple
from universal_silabs_flasher.gecko_bootloader import (
    XMODEM_BLOCK_SIZE,
    GeckoBootloaderOption,
    GeckoBootloaderProtocol,
    ReceiverCancelled,
    XModemPacketType,
)

if sys.version_info[:2] < (3, 11):
    from async_timeout import timeout as asyncio_timeout  # pragma: no cover
else:
    from asyncio import timeout as asyncio_timeout  # pragma: no cover


_LOGGER = logging.getLogger(__name__)

FIRMWARE = pad_to_multiple(
    (
        pathlib.Path(__file__).parent / "firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl"
    ).read_bytes(),
    XMODEM_BLOCK_SIZE,
    b"\xff",
)
assert len(FIRMWARE) % XMODEM_BLOCK_SIZE == 0


class PairedTransport(asyncio.Transport):
    """A pair of transports that are connected to each other."""

    def __init__(
        self,
        other_protocol: asyncio.Protocol,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        super().__init__()
        self._other_protocol = other_protocol
        self._loop = loop
        self._closing = False

    def write(self, data: bytes) -> None:
        _LOGGER.debug(
            "Writing to %s: %r", self._other_protocol.__class__.__name__, data
        )
        self._loop.call_soon(self._other_protocol.data_received, data)

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        _LOGGER.debug(
            "Closing transport for %s", self._other_protocol.__class__.__name__
        )
        self._loop.call_soon(self._other_protocol.connection_lost, None)


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
        _LOGGER.debug("Conversation received: %r", data)
        self._reader.feed_data(data)

    def connection_lost(self, exc: Exception | None) -> None:
        self._reader.feed_eof()

    async def send(self, data: bytes) -> None:
        assert self.transport is not None
        self.transport.write(data)

    async def send_menu(self) -> None:
        menu = (
            "\r\nGecko Bootloader v1.12.1\r\n"
            "1. upload gbl\r\n"
            "2. run\r\n"
            "3. ebl info\r\n"
            "BL > "
        ).encode("ascii")
        await self.send(menu)

    async def send_ack(self) -> None:
        await self.send(bytes([XModemPacketType.ACK]))

    async def send_nak(self) -> None:
        await self.send(bytes([XModemPacketType.NAK]))

    async def send_can(self) -> None:
        await self.send(bytes([XModemPacketType.CAN]))

    async def send_upload_complete(self) -> None:
        await self.send(b"\r\nSerial upload complete\r\n")

    async def expect_command(
        self,
        command: GeckoBootloaderOption,
        timeout: float = 1.0,
    ) -> None:
        """Expect a command, preceded by a newline."""
        async with asyncio_timeout(timeout):
            data = await self._reader.read(len(command) + 1)
        assert data.endswith(command)

    async def expect_packet(self, number: int, timeout: float = 2.0) -> bytes:
        """Read and validate a full XMODEM packet, returning its payload."""
        # 3 bytes header, 128 bytes payload, 2 bytes CRC
        async with asyncio_timeout(timeout):
            data = await self._reader.read(133)

        assert data[0] == XModemPacketType.SOH
        assert data[1] == number
        assert data[2] == 0xFF - number

        payload = data[3:-2]
        crc = int.from_bytes(data[-2:], "big")
        assert crc16_ccitt(payload) == crc

        return payload

    async def expect_eot(self, timeout: float = 1.0) -> None:
        async with asyncio_timeout(timeout):
            data = await self._reader.read(1)
        assert data == bytes([XModemPacketType.EOT])


async def create_test_pair() -> tuple[GeckoBootloaderProtocol, Conversation]:
    """Creates a connected pair of a client protocol and a conversation helper."""
    loop = asyncio.get_running_loop()
    client = GeckoBootloaderProtocol()
    conversation = Conversation(loop)

    client_transport = PairedTransport(conversation, loop)
    server_transport = PairedTransport(client, loop)

    client.connection_made(client_transport)
    conversation.connection_made(server_transport)

    return client, conversation


async def test_xmodem_happy_path() -> None:
    """Test a successful XMODEM transfer."""
    client, conversation = await create_test_pair()
    received_firmware = bytearray()

    # Start the upload and script the conversation
    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # The client automatically queries for info, so reply with a menu
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    for i in range(len(FIRMWARE) // XMODEM_BLOCK_SIZE):
        payload = await conversation.expect_packet(number=(i + 1) & 0xFF)
        received_firmware.extend(payload)
        await conversation.send_ack()

    await conversation.expect_eot()
    await conversation.send_ack()
    await conversation.send_upload_complete()

    # Final menu prompt
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    async with asyncio_timeout(1):
        await upload_task

    assert received_firmware == FIRMWARE


async def test_xmodem_with_retries() -> None:
    """Test an XMODEM transfer with some retries."""
    client, conversation = await create_test_pair()
    received_firmware = bytearray()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # First packet is OK
    payload = await conversation.expect_packet(number=1)
    received_firmware.extend(payload)
    await conversation.send_ack()

    # Second packet is NAK'd once
    await conversation.expect_packet(number=2)
    await conversation.send_nak()
    payload = await conversation.expect_packet(number=2)
    received_firmware.extend(payload)
    await conversation.send_ack()

    # The rest are OK
    for i in range(2, len(FIRMWARE) // XMODEM_BLOCK_SIZE):
        payload = await conversation.expect_packet(number=(i + 1) & 0xFF)
        received_firmware.extend(payload)
        await conversation.send_ack()

    await conversation.expect_eot()
    await conversation.send_ack()
    await conversation.send_upload_complete()

    # Final menu prompt
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    async with asyncio_timeout(1):
        await upload_task

    assert received_firmware == FIRMWARE


async def test_xmodem_timeout() -> None:
    """Test an XMODEM transfer with a receive timeout on the server side."""
    client, conversation = await create_test_pair()
    received_firmware = bytearray()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # First packet is OK
    payload = await conversation.expect_packet(number=1)
    received_firmware.extend(payload)
    await conversation.send_ack()

    # Do not reply to the second packet to trigger a timeout
    await conversation.expect_packet(number=2)
    payload = await conversation.expect_packet(number=2, timeout=3.0)
    received_firmware.extend(payload)
    await conversation.send_ack()

    # The rest are OK
    for i in range(2, len(FIRMWARE) // XMODEM_BLOCK_SIZE):
        payload = await conversation.expect_packet(number=(i + 1) & 0xFF)
        received_firmware.extend(payload)
        await conversation.send_ack()

    await conversation.expect_eot()
    await conversation.send_ack()
    await conversation.send_upload_complete()

    # Final menu prompt
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    async with asyncio_timeout(1):
        await upload_task

    assert received_firmware == FIRMWARE


async def test_xmodem_cancellation() -> None:
    """Test an XMODEM transfer that is cancelled by the receiver."""
    client, conversation = await create_test_pair()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # First packet is cancelled
    await conversation.expect_packet(number=1)
    await conversation.send_can()

    with pytest.raises(ReceiverCancelled):
        async with asyncio_timeout(1):
            await upload_task


async def test_xmodem_too_many_retries() -> None:
    """Test an XMODEM transfer that fails after too many retries."""
    client, conversation = await create_test_pair()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE, max_failures=3))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # Continuously NAK the first packet
    for i in range(4):
        await conversation.expect_packet(number=1)
        await conversation.send_nak()

    with pytest.raises(ValueError):
        async with asyncio_timeout(1):
            await upload_task


async def test_xmodem_multiple_c_bytes() -> None:
    """Test that the client handles multiple `C` bytes from the bootloader."""
    client, conversation = await create_test_pair()
    received_firmware = bytearray()

    # Start the upload and script the conversation
    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # The client automatically queries for info, so reply with a menu
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    # Send multiple `C` bytes
    await conversation.send(b"CCC")

    for i in range(len(FIRMWARE) // XMODEM_BLOCK_SIZE):
        payload = await conversation.expect_packet(number=(i + 1) & 0xFF)
        received_firmware.extend(payload)
        await conversation.send_ack()

    await conversation.expect_eot()
    await conversation.send_ack()
    await conversation.send_upload_complete()

    # Final menu prompt
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    async with asyncio_timeout(1):
        await upload_task

    assert received_firmware == FIRMWARE
