from __future__ import annotations

import asyncio
import dataclasses
import enum
import logging
import re
import typing

from zigpy.serial import SerialProtocol
import zigpy.types

from .common import PROBE_TIMEOUT, StateMachine, Version, asyncio_timeout, crc16_ccitt

_LOGGER = logging.getLogger(__name__)


class UploadError(Exception):
    pass


class NoFirmwareError(Exception):
    pass


class ReceiverCancelled(UploadError):
    """Receiver cancelled the transmission with a `CAN` status."""


MENU_AFTER_UPLOAD_TIMEOUT = 0.5
RUN_APPLICATION_DELAY = 0.1
XMODEM_BLOCK_SIZE = 128
XMODEM_RECEIVE_TIMEOUT = 2


class XModemPacketType(zigpy.types.enum8):
    """XModem packet type byte."""

    SOH = 0x01  # Start of Header
    EOT = 0x04  # End of Transmission
    CAN = 0x18  # Cancel
    ETB = 0x17  # End of Transmission Block
    ACK = 0x06  # Acknowledge
    NAK = 0x15  # Not Acknowledge


@dataclasses.dataclass(frozen=True)
class XmodemCRCPacket:
    """XModem CRC packet implementing the zigpy `serialize` API."""

    number: zigpy.types.uint8_t
    payload: bytes

    def serialize(self) -> bytes:
        """Serialize the packet, computing header and payload checksums."""
        assert len(self.payload) == XMODEM_BLOCK_SIZE
        return (
            bytes([XModemPacketType.SOH, self.number, 0xFF - self.number])
            + self.payload
            + crc16_ccitt(self.payload).to_bytes(2, "big")
        )


MENU_REGEX = re.compile(
    rb"\r\n(?P<type>Gecko|\w+ Serial) Bootloader v(?P<version>.*?)\r\n"
    rb"1\. upload (?:gbl|ebl)\r\n"
    rb"2\. run\r\n"
    rb"3\. ebl info\r\n"
    rb"(\d+\. .*?\r\n)*"  # All other options are ignored but we still expect a menu
    rb"BL > "
)

UPLOAD_STATUS_REGEX = re.compile(
    rb"\r\nSerial upload (?P<status>complete|aborted)\r\n"
    rb"(?P<message>.*?)\x00?",
    flags=re.DOTALL,
)  # fmt: skip


class State(str, enum.Enum):
    WAITING_FOR_MENU = "waiting_for_menu"
    IN_MENU = "in_menu"
    WAITING_XMODEM_READY = "waiting_xmodem_ready"
    XMODEM_UPLOADING = "xmodem_uploading"
    WAITING_UPLOAD_DONE = "waiting_upload_done"
    UPLOAD_DONE = "upload_done"


class GeckoBootloaderOption(bytes, enum.Enum):
    UPLOAD_FIRMWARE = b"1"
    RUN_FIRMWARE = b"2"
    EBL_INFO = b"3"


class GeckoBootloaderProtocol(SerialProtocol):
    _buffer: bytearray

    def __init__(self) -> None:
        super().__init__()
        self._state_machine = StateMachine(
            states=list(State),
            initial=State.WAITING_FOR_MENU,
        )
        self._version: str | None = None
        self._upload_status: str | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

        # XMODEM state
        self._xmodem_firmware: bytes | None = None
        self._xmodem_chunk_index: int = 0
        self._xmodem_total_chunks: int = 0
        self._xmodem_retries: int = 0
        self._xmodem_max_retries: int = 0
        self._xmodem_progress_callback: (
            typing.Callable[[int, int], typing.Any] | None
        ) = None
        self._xmodem_completion_future: asyncio.Future[None] | None = None
        self._xmodem_timeout_handle: asyncio.TimerHandle | None = None

    def connection_made(self, transport: asyncio.Transport) -> None:
        super().connection_made(transport)
        self.loop = asyncio.get_running_loop()

    def connection_lost(self, exc: Exception | None) -> None:
        super().connection_lost(exc)
        self._state_machine.cancel_all_futures(
            exc or RuntimeError("Connection has been lost")
        )

        if self._xmodem_completion_future and not self._xmodem_completion_future.done():
            self._xmodem_completion_future.set_exception(
                exc or RuntimeError("Connection has been lost")
            )

        if self._xmodem_timeout_handle:
            self._xmodem_timeout_handle.cancel()

    async def probe(self) -> Version:
        """Attempt to communicate with the bootloader."""
        async with asyncio_timeout(PROBE_TIMEOUT):
            return await self.ebl_info()

    async def ebl_info(self) -> Version:
        """Select `ebl info` in the menu and return the bootloader version."""
        self._state_machine.state = State.WAITING_FOR_MENU

        # Ember bootloader requires a newline
        self.send_data(b"\n")
        self.send_data(GeckoBootloaderOption.EBL_INFO)

        await self._state_machine.wait_for_state(State.IN_MENU)

        assert self._version is not None
        return Version(self._version)

    async def run_firmware(self) -> None:
        """Select `run` in the menu."""
        await self._state_machine.wait_for_state(State.IN_MENU)

        # If the firmware fails to launch, the menu will appear again
        self._state_machine.state = State.WAITING_FOR_MENU
        self.send_data(GeckoBootloaderOption.RUN_FIRMWARE)

        try:
            async with asyncio_timeout(RUN_APPLICATION_DELAY):
                await self._state_machine.wait_for_state(State.IN_MENU)
        except asyncio.TimeoutError:
            # The menu did not appear so the application must be running
            return
        else:
            raise NoFirmwareError("No firmware exists on the device")

    def _xmodem_timeout_cb(self) -> None:
        """XMODEM receive timeout."""
        self._xmodem_timeout_handle = None
        _LOGGER.debug("XMODEM receive timeout")
        self._xmodem_retry_chunk()

    def _xmodem_retry_chunk(self) -> None:
        """Retry sending the current XMODEM chunk."""
        if self._xmodem_retries >= self._xmodem_max_retries:
            self._xmodem_abort(
                UploadError(f"Received {self._xmodem_max_retries} consecutive failures")
            )
            return

        self._xmodem_retries += 1
        _LOGGER.debug(
            "Retrying chunk %d (attempt %d)",
            self._xmodem_chunk_index,
            self._xmodem_retries,
        )
        self._xmodem_send_chunk_or_eot()

    def _xmodem_abort(self, exc: Exception) -> None:
        """Abort XMODEM transfer."""
        if self._xmodem_completion_future and not self._xmodem_completion_future.done():
            self._xmodem_completion_future.set_exception(exc)

        if self._xmodem_timeout_handle:
            self._xmodem_timeout_handle.cancel()
            self._xmodem_timeout_handle = None

    def _xmodem_send_chunk_or_eot(self) -> None:
        """Send the current XMODEM chunk or EOT if done."""
        if self._xmodem_chunk_index >= self._xmodem_total_chunks:
            _LOGGER.debug("Sending EOT")
            self.send_data(bytes([XModemPacketType.EOT]))
        else:
            _LOGGER.debug("Sending chunk %d", self._xmodem_chunk_index)
            assert self._xmodem_firmware is not None
            packet = XmodemCRCPacket(
                number=(self._xmodem_chunk_index + 1) & 0xFF,
                payload=self._xmodem_firmware[
                    XMODEM_BLOCK_SIZE * self._xmodem_chunk_index : XMODEM_BLOCK_SIZE
                    * (self._xmodem_chunk_index + 1)
                ],
            )
            self.send_data(packet.serialize())

        assert self.loop is not None
        self._xmodem_timeout_handle = self.loop.call_later(
            XMODEM_RECEIVE_TIMEOUT, self._xmodem_timeout_cb
        )

    async def upload_firmware(
        self,
        firmware: bytes,
        *,
        max_failures: int = 3,
        progress_callback: typing.Callable[[int, int], typing.Any] | None = None,
    ) -> None:
        """Select `upload gbl` in the menu and upload GBL firmware."""
        await self.ebl_info()

        # Select the option
        self._state_machine.state = State.WAITING_XMODEM_READY
        self.send_data(GeckoBootloaderOption.UPLOAD_FIRMWARE)

        # Wait for the XMODEM `C` byte
        await self._state_machine.wait_for_state(State.XMODEM_UPLOADING)

        # Set up XMODEM state
        self._xmodem_firmware = firmware
        self._xmodem_chunk_index = 0
        self._xmodem_total_chunks = len(firmware) // XMODEM_BLOCK_SIZE
        self._xmodem_retries = 0
        self._xmodem_max_retries = max_failures
        self._xmodem_progress_callback = progress_callback
        assert self.loop is not None
        self._xmodem_completion_future = self.loop.create_future()

        if self._xmodem_progress_callback is not None:
            self._xmodem_progress_callback(0, len(self._xmodem_firmware))

        # Start the transfer
        self._xmodem_send_chunk_or_eot()

        # Wait for transfer to complete
        await self._xmodem_completion_future

        # Clean up XMODEM state
        self._xmodem_firmware = None
        self._xmodem_completion_future = None

        # After XMODEM completes, data_received processes the upload status message
        # and transitions: UPLOAD_DONE -> WAITING_FOR_MENU -> IN_MENU.
        # (if menu is buffered). The menu is sometimes sent immediately after upload.
        try:
            async with asyncio_timeout(MENU_AFTER_UPLOAD_TIMEOUT):
                await self._state_machine.wait_for_state(State.IN_MENU)
        except asyncio.TimeoutError:
            # If not, trigger it manually
            await self.ebl_info()

        if self._upload_status != "complete":
            raise UploadError(self._upload_status)

    def send_data(self, data: bytes) -> None:
        assert self._transport is not None
        _LOGGER.debug("Sending data %s", data)
        self._transport.write(data)

    def _handle_xmodem_response(self) -> None:
        """Handle a single byte response from the receiver."""
        if not self._buffer:
            return

        # If we are not waiting for a response, this is spurious data
        if self._xmodem_timeout_handle is None:
            _LOGGER.debug("Ignoring spurious XMODEM data: %r", self._buffer)
            self._buffer.clear()
            return

        # We are waiting for a response, so cancel the timeout
        self._xmodem_timeout_handle.cancel()
        self._xmodem_timeout_handle = None

        response = self._buffer[0]
        self._buffer = self._buffer[1:]

        if response == XModemPacketType.ACK:
            if self._xmodem_chunk_index >= self._xmodem_total_chunks:
                # EOT was ACKed - keep buffer intact as upload complete/menu may follow
                if (
                    self._xmodem_completion_future
                    and not self._xmodem_completion_future.done()
                ):
                    self._xmodem_completion_future.set_result(None)
                self._state_machine.state = State.WAITING_UPLOAD_DONE
                # Process the rest of the buffer for the upload complete message
                self.data_received(b"")
                return

            assert self._xmodem_firmware is not None
            offset = (self._xmodem_chunk_index + 1) * XMODEM_BLOCK_SIZE
            if self._xmodem_progress_callback is not None:
                self._xmodem_progress_callback(offset, len(self._xmodem_firmware))

            _LOGGER.debug(
                "Firmware upload progress: %0.2f%%",
                100 * offset / len(self._xmodem_firmware),
            )

            self._xmodem_chunk_index += 1
            self._xmodem_retries = 0
            self._xmodem_send_chunk_or_eot()
        elif response == XModemPacketType.NAK:
            _LOGGER.debug("Got a NAK, retrying")
            self._xmodem_retry_chunk()
        elif response == XModemPacketType.CAN:
            self._xmodem_abort(ReceiverCancelled())
        else:
            _LOGGER.warning("Invalid XMODEM response: %r", response)
            # Treat as a failure and retry
            self._xmodem_retry_chunk()

    def data_received(self, data: bytes) -> None:
        super().data_received(data)

        if self._state_machine.state == State.XMODEM_UPLOADING:
            self._handle_xmodem_response()
            return

        while self._buffer:
            _LOGGER.debug("Parsing %s: %r", self._state_machine.state, self._buffer)
            current_state = self._state_machine.state

            if current_state == State.WAITING_FOR_MENU:
                match = MENU_REGEX.search(self._buffer)

                if match is None:
                    break

                self._version = match.group("version").decode("ascii")
                _LOGGER.debug("Detected version string %r", self._version)

                self._buffer.clear()
                self._state_machine.state = State.IN_MENU
            elif current_state == State.WAITING_XMODEM_READY:
                if not self._buffer.endswith(b"C"):
                    break

                self._buffer.clear()
                self._state_machine.state = State.XMODEM_UPLOADING
            elif current_state == State.WAITING_UPLOAD_DONE:
                match = UPLOAD_STATUS_REGEX.search(self._buffer)

                if match is None:
                    break

                status = match.group("status").decode("ascii")

                if status == "complete":
                    self._upload_status = status
                else:
                    self._upload_status = match.group("message").decode("ascii")

                del self._buffer[: match.span()[1]]
                self._state_machine.state = State.UPLOAD_DONE

                _LOGGER.debug("Upload status: %s", self._upload_status)
            elif current_state == State.UPLOAD_DONE:
                # Transition to waiting for menu and continue processing buffer
                self._state_machine.state = State.WAITING_FOR_MENU

            # If the state changed, re-evaluate the loop with the new state
            if self._state_machine.state != current_state:
                continue

            # Otherwise, we need more data
            break
