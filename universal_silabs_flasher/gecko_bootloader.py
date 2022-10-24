from __future__ import annotations

import re
import enum
import typing
import asyncio
import logging

from .common import StateMachine, SerialProtocol
from .xmodemcrc import send_xmodem128_crc

_LOGGER = logging.getLogger(__name__)


class UploadError(Exception):
    pass


RUN_APPLICATION_DELAY = 0.5
MENU_REGEX = re.compile(
    rb"\r\nGecko Bootloader v(?P<version>.*?)\r\n"
    rb"1\. upload gbl\r\n"
    rb"2\. run\r\n"
    rb"3\. ebl info\r\n"
    rb"BL > "
)

UPLOAD_STATUS_REGEX = re.compile(
    rb"\r\nSerial upload (?P<status>complete|aborted)\r\n(?P<abort_status>.*?)\x00",
    flags=re.DOTALL,
)


class State(str, enum.Enum):
    WAITING_FOR_MENU = "waiting_for_menu"
    IN_MENU = "in_menu"
    WAITING_XMODEM_READY = "waiting_xmodem_ready"
    XMODEM_READY = "xmodem_ready"
    WAITING_UPLOAD_DONE = "waiting_upload_done"
    RUNNING_FIRMWARE = "running_firmware"


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

    async def probe(self):
        return await self.ebl_info(wait_for_menu=False)

    async def ebl_info(self, *, wait_for_menu: bool = True):
        if wait_for_menu:
            await self._state_machine.wait_for_state(State.IN_MENU)

        self._state_machine.set_state(State.WAITING_FOR_MENU)
        self.send_data(GeckoBootloaderOption.EBL_INFO)

        await self._state_machine.wait_for_state(State.IN_MENU)

        assert self._version is not None
        return self._version

    async def run_firmware(self):
        await self._state_machine.wait_for_state(State.IN_MENU)
        self._state_machine.set_state(State.RUNNING_FIRMWARE)
        self.send_data(GeckoBootloaderOption.RUN_FIRMWARE)
        await asyncio.sleep(RUN_APPLICATION_DELAY)

    async def upload_firmware(
        self,
        firmware: bytes,
        *,
        max_failures: int = 3,
        progress_callback: typing.Callable[[int, int], typing.Any] | None = None,
    ) -> None:
        await self._state_machine.wait_for_state(State.IN_MENU)

        _LOGGER.debug("Choosing GBL upload")
        self._state_machine.set_state(State.WAITING_XMODEM_READY)
        self.send_data(GeckoBootloaderOption.UPLOAD_GBL)

        _LOGGER.debug("Waiting for XMODEM to be ready")
        await self._state_machine.wait_for_state(State.XMODEM_READY)

        _LOGGER.debug("Beginning XMODEM transfer")
        await send_xmodem128_crc(
            firmware,
            transport=self._transport,
            max_failures=max_failures,
            progress_callback=progress_callback,
        )

        self._state_machine.set_state(State.WAITING_UPLOAD_DONE)
        await self._state_machine.wait_for_state(State.IN_MENU)

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
                self._state_machine.set_state(State.IN_MENU)
            elif self._state_machine.state == State.WAITING_XMODEM_READY:
                if b"\r\nbegin upload\r\n\x00" not in self._buffer:
                    break

                self._buffer.clear()
                self._state_machine.set_state(State.XMODEM_READY)
            elif self._state_machine.state == State.WAITING_UPLOAD_DONE:
                match = UPLOAD_STATUS_REGEX.search(self._buffer)

                if match is None:
                    return

                status = match.group("status").decode("ascii")

                if status != "complete":
                    status = match.group("abort_status")
                    raise UploadError(status)

                del self._buffer[: match.span()[1]]
                self._state_machine.set_state(State.WAITING_FOR_MENU)
            else:
                # Ignore data otherwise
                break
