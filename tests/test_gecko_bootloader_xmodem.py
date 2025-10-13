from __future__ import annotations

import asyncio
import logging
import pathlib
import sys
from unittest.mock import MagicMock, call

import pytest

from universal_silabs_flasher.common import crc16_ccitt, pad_to_multiple
from universal_silabs_flasher.gecko_bootloader import (
    XMODEM_BLOCK_SIZE,
    GeckoBootloaderOption,
    GeckoBootloaderProtocol,
    ReceiverCancelled,
    UploadError,
    XModemPacketType,
)

if sys.version_info[:2] < (3, 11):
    from async_timeout import timeout as asyncio_timeout  # pragma: no cover
else:
    from asyncio import timeout as asyncio_timeout  # pragma: no cover


_LOGGER = logging.getLogger(__name__)

FULL_FIRMWARE = pad_to_multiple(
    (
        pathlib.Path(__file__).parent / "firmwares/skyconnect_zigbee_ncp_7.4.4.0.gbl"
    ).read_bytes(),
    XMODEM_BLOCK_SIZE,
    b"\xff",
)

FIRMWARE = FULL_FIRMWARE[: 100 * XMODEM_BLOCK_SIZE]


class PairedTransport(asyncio.Transport):
    """A pair of transports that are connected to each other."""

    def __init__(
        self,
        other_protocol: asyncio.Protocol,
        loop: asyncio.AbstractEventLoop,
        chunk_size: int | None = None,
        aggregate_write_timeout: float | None = None,
    ) -> None:
        super().__init__()
        self._other_protocol = other_protocol
        self._loop = loop
        self._closing = False
        self._chunk_size = chunk_size
        self._aggregate_write_timeout = aggregate_write_timeout
        self._aggregate_buffer = bytearray()
        self._aggregate_timer_handle: asyncio.TimerHandle | None = None

    def _flush_aggregate_buffer(self) -> None:
        """Flush the aggregated write buffer."""
        self._aggregate_timer_handle = None

        if not self._aggregate_buffer:
            return

        data = bytes(self._aggregate_buffer)
        self._aggregate_buffer.clear()

        _LOGGER.debug(
            "Flushing aggregated data to %s: %r",
            self._other_protocol.__class__.__name__,
            data,
        )

        if self._chunk_size is None:
            # Send all at once
            self._loop.call_soon(self._other_protocol.data_received, data)
        else:
            # Send in chunks of specified size
            for i in range(0, len(data), self._chunk_size):
                chunk = data[i : i + self._chunk_size]
                self._loop.call_soon(self._other_protocol.data_received, chunk)

    def write(self, data: bytes) -> None:
        _LOGGER.debug(
            "Writing to %s: %r", self._other_protocol.__class__.__name__, data
        )

        if self._aggregate_write_timeout is None:
            # No aggregation, send immediately
            if self._chunk_size is None:
                # Send all at once (default behavior)
                self._loop.call_soon(self._other_protocol.data_received, data)
            else:
                # Send in chunks of specified size
                for i in range(0, len(data), self._chunk_size):
                    chunk = data[i : i + self._chunk_size]
                    self._loop.call_soon(self._other_protocol.data_received, chunk)
        else:
            # Aggregate writes within timeout window
            self._aggregate_buffer.extend(data)

            # Cancel existing timer and schedule a new one
            if self._aggregate_timer_handle is not None:
                self._aggregate_timer_handle.cancel()

            self._aggregate_timer_handle = self._loop.call_later(
                self._aggregate_write_timeout, self._flush_aggregate_buffer
            )

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True

        # Flush any pending aggregated data
        if self._aggregate_timer_handle is not None:
            self._aggregate_timer_handle.cancel()
            self._aggregate_timer_handle = None

        if self._aggregate_buffer:
            self._flush_aggregate_buffer()

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


async def create_test_pair(
    chunk_size: int | None = None,
    aggregate_write_timeout: float | None = None,
) -> tuple[GeckoBootloaderProtocol, Conversation]:
    """Creates a connected pair of a client protocol and a conversation helper."""
    loop = asyncio.get_running_loop()
    client = GeckoBootloaderProtocol()
    conversation = Conversation(loop)

    client_transport = PairedTransport(
        conversation,
        loop,
        chunk_size=chunk_size,
        aggregate_write_timeout=aggregate_write_timeout,
    )
    server_transport = PairedTransport(
        client,
        loop,
        chunk_size=chunk_size,
        aggregate_write_timeout=aggregate_write_timeout,
    )

    client.connection_made(client_transport)
    conversation.connection_made(server_transport)

    return client, conversation


async def test_xmodem_happy_path() -> None:
    """Test a successful XMODEM transfer."""
    client, conversation = await create_test_pair()
    received_firmware = bytearray()
    progress_callback = MagicMock()

    # Start the upload and script the conversation
    upload_task = asyncio.create_task(
        client.upload_firmware(FULL_FIRMWARE, progress_callback=progress_callback)
    )

    # The client automatically queries for info, so reply with a menu
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    for i in range(len(FULL_FIRMWARE) // XMODEM_BLOCK_SIZE):
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

    assert received_firmware == FULL_FIRMWARE
    assert progress_callback.mock_calls == [
        call(i * XMODEM_BLOCK_SIZE, len(FULL_FIRMWARE))
        for i in range(1 + len(FULL_FIRMWARE) // XMODEM_BLOCK_SIZE)
    ]


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

    with pytest.raises(UploadError):
        await upload_task


async def test_xmodem_ack_with_garbage() -> None:
    """Test that the client handles invalid XMODEM responses."""
    client, conversation = await create_test_pair()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # First packet is OK
    await conversation.expect_packet(number=1)
    await conversation.send_ack()

    # Send invalid responses (not ACK, NAK, or CAN) - should retry and eventually fail
    for i in range(4):
        await conversation.expect_packet(number=2)
        await conversation.send(b"\xff")  # Invalid response

    # The upload fails due to too many retries
    with pytest.raises(UploadError, match="Received 3 consecutive failures"):
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


async def test_xmodem_spurious_ack() -> None:
    """Test that the client ignores a spurious ACK."""
    client, conversation = await create_test_pair()
    received_firmware = bytearray()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # Send a spurious ACK, client should ignore it and send packet 1
    await conversation.send_ack()

    # The rest of the transfer should proceed normally
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


async def test_xmodem_spurious_nak() -> None:
    """Test that the client ignores a spurious NAK."""
    client, conversation = await create_test_pair()
    received_firmware = bytearray()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # Send a spurious NAK, client should ignore it and send packet 1
    await conversation.send_nak()

    # The rest of the transfer should proceed normally
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


async def test_xmodem_task_cancellation() -> None:
    """Test that the client handles task cancellation."""
    client, conversation = await create_test_pair()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # First packet is OK
    await conversation.expect_packet(number=1)
    await conversation.send_ack()

    # Cancel the task during the transfer
    upload_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await upload_task


async def test_xmodem_connection_lost() -> None:
    """Test that the client handles connection loss during XMODEM transfer."""
    client, conversation = await create_test_pair()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # First packet is OK
    await conversation.expect_packet(number=1)
    await conversation.send_ack()

    # Second packet is sent, but connection is lost before response
    await conversation.expect_packet(number=2)

    # Simulate connection loss (disconnect) by calling connection_lost directly
    client.connection_lost(None)

    # The upload should fail with connection lost error
    with pytest.raises(RuntimeError, match="Connection has been lost"):
        await upload_task


async def test_xmodem_can_during_transfer() -> None:
    """Test that the client handles CAN (cancel) byte during transfer."""
    client, conversation = await create_test_pair()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # First packet is OK
    await conversation.expect_packet(number=1)
    await conversation.send_ack()

    # Second packet gets a CAN response
    await conversation.expect_packet(number=2)
    await conversation.send(bytes([XModemPacketType.CAN]))

    # The upload should fail with ReceiverCancelled
    with pytest.raises(ReceiverCancelled):
        await upload_task


async def test_xmodem_empty_buffer_during_transfer() -> None:
    """Test that empty data during XMODEM transfer is handled gracefully."""
    client, conversation = await create_test_pair()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # First packet is OK
    await conversation.expect_packet(number=1)
    # Send empty data (should be ignored due to empty buffer check)
    await conversation.send(b"")
    # Then send ACK
    await conversation.send_ack()

    # Continue with rest of transfer
    for i in range(1, len(FIRMWARE) // XMODEM_BLOCK_SIZE):
        await conversation.expect_packet(number=(i + 1) & 0xFF)
        await conversation.send_ack()

    await conversation.expect_eot()
    await conversation.send_ack()
    await conversation.send_upload_complete()
    await conversation.send_menu()

    async with asyncio_timeout(1):
        await upload_task


async def test_xmodem_abort_with_pending_timeout() -> None:
    """Test that abort properly cancels pending timeout."""
    client, conversation = await create_test_pair()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # First packet - send it but don't respond yet
    await conversation.expect_packet(number=1)

    # At this point, a timeout is pending. Manually trigger abort.
    # This exercises the timeout cancellation in _xmodem_abort()
    error = UploadError("Manual abort test")
    client._xmodem_abort(error)

    # The upload should fail with our error
    with pytest.raises(UploadError, match="Manual abort test"):
        await upload_task


async def test_xmodem_reverts_to_line_parsing() -> None:
    """Test that the client reverts to line-based parsing after an upload."""
    client, conversation = await create_test_pair()
    received_firmware = bytearray()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # Full transfer
    for i in range(len(FIRMWARE) // XMODEM_BLOCK_SIZE):
        payload = await conversation.expect_packet(number=(i + 1) & 0xFF)
        received_firmware.extend(payload)
        await conversation.send_ack()

    await conversation.expect_eot()
    await conversation.send_ack()

    # Send the upload complete and menu messages back-to-back
    await conversation.send_upload_complete()
    await conversation.send_menu()

    # The upload task should complete successfully
    async with asyncio_timeout(1):
        await upload_task

    assert received_firmware == FIRMWARE
    assert client._state_machine.state == "in_menu"


async def test_xmodem_reverts_to_line_parsing_byte_by_byte() -> None:
    """Test line-based parsing after upload with byte-by-byte delivery."""
    client, conversation = await create_test_pair(chunk_size=1)
    received_firmware = bytearray()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # Full transfer
    for i in range(len(FIRMWARE) // XMODEM_BLOCK_SIZE):
        payload = await conversation.expect_packet(number=(i + 1) & 0xFF)
        received_firmware.extend(payload)
        await conversation.send_ack()

    await conversation.expect_eot()
    await conversation.send_ack()

    # Send the upload complete and menu messages back-to-back
    await conversation.send_upload_complete()
    await conversation.send_menu()

    # The upload task should complete successfully
    async with asyncio_timeout(1):
        await upload_task

    assert received_firmware == FIRMWARE
    assert client._state_machine.state == "in_menu"


async def test_xmodem_reverts_to_line_parsing_with_write_aggregation() -> None:
    """Test line-based parsing after upload with write aggregation."""
    client, conversation = await create_test_pair(aggregate_write_timeout=0.05)
    received_firmware = bytearray()

    upload_task = asyncio.create_task(client.upload_firmware(FIRMWARE))

    # Initial info query
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()

    # Upload command
    await conversation.expect_command(GeckoBootloaderOption.UPLOAD_FIRMWARE)
    await conversation.send(b"C")

    # Full transfer
    for i in range(len(FIRMWARE) // XMODEM_BLOCK_SIZE):
        payload = await conversation.expect_packet(number=(i + 1) & 0xFF)
        received_firmware.extend(payload)
        await conversation.send_ack()

    await conversation.expect_eot()
    await conversation.send_ack()

    # Send the upload complete and menu messages back-to-back
    # With write aggregation, these should be buffered and delivered together
    await conversation.send_upload_complete()
    await conversation.send_menu()

    # The upload task should complete successfully
    async with asyncio_timeout(1):
        await upload_task

    assert received_firmware == FIRMWARE
    assert client._state_machine.state == "in_menu"


async def test_parser_needs_more_data() -> None:
    """Test that parser correctly waits when it needs more data."""
    client, conversation = await create_test_pair()

    # Start the ebl_info query
    info_task = asyncio.create_task(client.ebl_info())

    # Expect the command
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)

    # Send partial menu data that won't match the regex yet
    partial_menu = b"\r\nGecko Bootloader v1.12.1\r\n1. upload"
    await conversation.send(partial_menu)

    # Give the parser a moment to process
    await asyncio.sleep(0.1)

    # The state should still be WAITING_FOR_MENU since we don't have a complete menu
    assert client._state_machine.state == "waiting_for_menu"

    # Now send the rest of the menu
    rest_of_menu = b" gbl\r\n2. run\r\n3. ebl info\r\nBL > "
    await conversation.send(rest_of_menu)

    # The info task should complete
    async with asyncio_timeout(1):
        await info_task


async def test_spurious_data_in_menu() -> None:
    """Test that spurious data while in IN_MENU state is ignored."""
    client, conversation = await create_test_pair()

    # Get into IN_MENU state
    info_task = asyncio.create_task(client.ebl_info())
    await conversation.expect_command(GeckoBootloaderOption.EBL_INFO)
    await conversation.send_menu()
    await info_task

    # Now we're in IN_MENU state. Send some spurious data.
    await conversation.send(b"xyz")

    # Give the parser a moment to process
    await asyncio.sleep(0.1)

    # Should still be in IN_MENU, just ignoring the spurious data
    assert client._state_machine.state == "in_menu"
