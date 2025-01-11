from __future__ import annotations

import asyncio
import enum
import logging
import re

from zigpy.serial import SerialProtocol

from .common import PROBE_TIMEOUT, StateMachine, Version, asyncio_timeout

_LOGGER = logging.getLogger(__name__)

ROUTER_INFO_REGEX = re.compile(rb"stack ver\. \[(?P<version>.*?)\]\r\n")


class State(str, enum.Enum):
    STARTUP = "startup"
    BOOTWAIT = "bootwait"
    INFO = "info"
    READY = "ready"


class RouterCommand(bytes, enum.Enum):
    INFO = b"version\r\n"
    BL_REBOOT = b"bootloader reboot\r\n"


class RouterProtocol(SerialProtocol):
    def __init__(self) -> None:
        super().__init__()
        self._state_machine = StateMachine(
            states=list(State),
            initial=State.STARTUP,
        )
        self._version: str | None = None

    async def probe(self) -> Version:
        """Attempt to communicate with the router."""
        async with asyncio_timeout(PROBE_TIMEOUT):
            return await self.router_info()

    async def router_info(self) -> Version:
        """Get the router version."""
        await self.activate_prompt()
        self._state_machine.state = State.INFO
        self.send_data(RouterCommand.INFO)

        await self._state_machine.wait_for_state(State.READY)

        assert self._version is not None
        return Version(self._version)

    async def activate_prompt(self) -> None:
        """Send enter key to activate CLI prompt."""
        if self._state_machine.state == State.STARTUP:
            await asyncio.sleep(0.5)
            self.send_data(b"\r\n")
            await self._state_machine.wait_for_state(State.READY)

    def send_data(self, data: bytes) -> None:
        assert self._transport is not None
        _LOGGER.debug("Sending data %s", data)
        self._transport.write(data)

    def data_received(self, data: bytes) -> None:
        super().data_received(data)

        while self._buffer:
            _LOGGER.debug("Parsing %s: %r", self._state_machine.state, self._buffer)
            if self._state_machine.state == State.STARTUP:
                if b">" not in self._buffer:
                    return

                self._buffer.clear()
                self._state_machine.state = State.READY

            if self._state_machine.state == State.INFO:
                match = ROUTER_INFO_REGEX.search(self._buffer)

                if match is None:
                    return

                self._version = match.group("version").decode("ascii")
                _LOGGER.debug("Detected version string %r", self._version)

                self._buffer.clear()
                self._state_machine.state = State.READY

            elif self._state_machine.state == State.BOOTWAIT:
                if b"Gecko Bootloader" not in self._buffer:
                    return

                _LOGGER.debug("Bootloader started")

                self._buffer.clear()
                self._state_machine.state = State.READY

            elif self._state_machine.state == State.READY:
                self._buffer.clear()

    async def enter_bootloader(self) -> None:
        await self.activate_prompt()
        self._state_machine.state = State.BOOTWAIT

        self.send_data(RouterCommand.BL_REBOOT)
        await self._state_machine.wait_for_state(State.READY)
