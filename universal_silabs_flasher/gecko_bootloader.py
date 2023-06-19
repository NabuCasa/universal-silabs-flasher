from __future__ import annotations

import asyncio
import enum
import logging
import re
import typing

import async_timeout

from .common import PROBE_TIMEOUT, SerialProtocol, StateMachine, Version
from .xmodemcrc import send_xmodem128_crc

_LOGGER = logging.getLogger(__name__)


class UploadError(Exception):
    pass


class NoFirmwareError(Exception):
    pass


MENU_AFTER_UPLOAD_TIMEOUT = 0.5
RUN_APPLICATION_DELAY = 0.1

MENU_REGEX = re.compile(
    rb"\r\nGecko Bootloader v(?P<version>.*?)\r\n"
    rb"1\. upload gbl\r\n"
    rb"2\. run\r\n"
    rb"3\. ebl info\r\n"
    rb"BL > "
)

UPLOAD_STATUS_REGEX = re.compile(
    rb"\r\nSerial upload (?P<status>complete|aborted)\r\n"
    rb"(?P<message>.*?)\x00",
    flags=re.DOTALL,
)  # fmt: skip


class State(str, enum.Enum):
    WAITING_FOR_MENU = "waiting_for_menu"
    IN_MENU = "in_menu"
    WAITING_XMODEM_READY = "waiting_xmodem_ready"
    XMODEM_READY = "xmodem_ready"
    WAITING_UPLOAD_DONE = "waiting_upload_done"


class GeckoBootloaderOption(bytes, enum.Enum):
    UPLOAD_GBL = b"1"
    RUN_FIRMWARE = b"2"
    EBL_INFO = b"3"


class GeckoBootloaderProtocol(SerialProtocol):
    def __init__(self) -> None:
        super().__init__()
        self._state_machine = StateMachine(
            states=list(State),
            initial=State.WAITING_FOR_MENU,
        )
        self._version: str | None = None
        self._upload_status: str | None = None

    async def probe(self) -> Version:
        """Attempt to communicate with the bootloader."""
        async with async_timeout.timeout(PROBE_TIMEOUT):
            return await self.ebl_info()

    async def ebl_info(self) -> Version:
        """Select `ebl info` in the menu and return the bootloader version."""
        self._state_machine.state = State.WAITING_FOR_MENU
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
            async with async_timeout.timeout(RUN_APPLICATION_DELAY):
                await self._state_machine.wait_for_state(State.IN_MENU)
        except asyncio.TimeoutError:
            # The menu did not appear so the application must be running
            return
        else:
            raise NoFirmwareError("No firmware exists on the device")

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
        self.send_data(GeckoBootloaderOption.UPLOAD_GBL)

        # Wait for the XMODEM `C` byte
        await self._state_machine.wait_for_state(State.XMODEM_READY)

        # Swap protocols and transfer the data
        self._upload_status = None
        self._state_machine.state = State.WAITING_UPLOAD_DONE

        await send_xmodem128_crc(
            firmware,
            transport=self._transport,
            max_failures=max_failures,
            progress_callback=progress_callback,
        )

        await self._state_machine.wait_for_state(State.IN_MENU)

        if self._upload_status != "complete":
            raise UploadError(self._upload_status)

    def data_received(self, data: bytes) -> None:
        super().data_received(data)

        while self._buffer:
            _LOGGER.debug("Parsing %s: %r", self._state_machine.state, self._buffer)
            if self._state_machine.state == State.WAITING_FOR_MENU:
                match = MENU_REGEX.search(self._buffer)

                if match is None:
                    return

                self._version = match.group("version").decode("ascii")
                _LOGGER.debug("Detected version string %r", self._version)

                self._buffer.clear()
                self._state_machine.state = State.IN_MENU
            elif self._state_machine.state == State.WAITING_XMODEM_READY:
                if b"\r\nbegin upload\r\n\x00" not in self._buffer:
                    break

                self._buffer.clear()
                self._state_machine.state = State.XMODEM_READY
            elif self._state_machine.state == State.WAITING_UPLOAD_DONE:
                match = UPLOAD_STATUS_REGEX.search(self._buffer)

                if match is None:
                    return

                status = match.group("status").decode("ascii")

                if status == "complete":
                    self._upload_status = status
                else:
                    self._upload_status = match.group("message").decode("ascii")

                del self._buffer[: match.span()[1]]
                self._state_machine.state = State.WAITING_FOR_MENU
            else:
                # Ignore data otherwise
                break
