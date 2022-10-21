from __future__ import annotations

import re
import enum
import logging

from .common import StateMachine, SerialProtocol

_LOGGER = logging.getLogger(__name__)


MENU_REGEX = re.compile(
    rb"\r\n"
    rb"Gecko Bootloader v(?P<version>.*?)\r\n"
    rb"1\. upload gbl\r\n"
    rb"2\. run\r\n"
    rb"3\. ebl info\r\n"
    rb"BL > \x00$"
)


class State(str, enum.Enum):
    WAITING_FOR_MENU = "waiting_for_menu"
    IN_MENU = "in_menu"
    WAITING_FOR_XMODEM_READY = "waiting_for_xmodem_ready"
    XMODEM_READY = "xmodem_ready"
    RUNNING_FIRMWARE = "running_firmware"


class GeckoBootloaderOption(bytes, enum.Enum):
    UPLOAD_GBL = b"1"
    RUN_FIRMWARE = b"2"
    EBL_INFO = b"3"  # does nothing


class GeckoBootloaderProtocol(SerialProtocol):
    def __init__(self) -> None:
        super().__init__()
        self._state_machine = StateMachine(
            states=list(State._members_),
            initial=State.WAITING_FOR_MENU,
        )
        self._version: str | None = None

    async def probe(self):
        await self.choose_option(GeckoBootloaderOption.EBL_INFO)
        return await self.wait_for_menu()

    async def choose_option(self, option: GeckoBootloaderOption) -> None:
        if option == GeckoBootloaderOption.EBL_INFO:
            self._state_machine.set_state(State.WAITING_FOR_MENU)
            self.send_data(option)
        elif option == GeckoBootloaderOption.RUN_FIRMWARE:
            self._state_machine.set_state(State.RUNNING_FIRMWARE)
            self.send_data(option)
        elif option == GeckoBootloaderOption.UPLOAD_GBL:
            self._state_machine.set_state(State.WAITING_FOR_XMODEM_READY)
            self.send_data(option)

            await self._state_machine.wait_for_state("xmodem_ready")
        else:
            raise ValueError(f"Invalid option: {option!r}")

    async def wait_for_menu(self) -> str:
        await self._state_machine.wait_for_state("in_menu")

        assert self._version is not None
        return self._version

    def data_received(self, data: bytes) -> None:
        super().data_received(data)

        if self._state_machine.state == State.WAITING_FOR_MENU:
            match = MENU_REGEX.match(self._buffer)

            if match is None:
                return

            self._version = match.group("version").decode("ascii")
            _LOGGER.debug("Detected version string %r", self._version)

            self._buffer.clear()
            self._state_machine.set_state(State.IN_MENU)
        elif self._state_machine.state == State.WAITING_FOR_XMODEM_READY:
            if b"C" in self._buffer:
                self._buffer.clear()
                self._state_machine.set_state(State.XMODEM_READY)
        else:
            # Ignore data otherwise
            pass
