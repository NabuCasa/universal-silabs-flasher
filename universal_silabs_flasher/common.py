from __future__ import annotations

import typing
import asyncio
import logging
import collections

import serial_asyncio

_LOGGER = logging.getLogger(__name__)


class StateMachine:
    def __init__(self, states: list[str], initial: str) -> None:
        assert initial in states

        self.states = states
        self.state = initial

        self.futures_for_state: typing.DefaultDict[
            str, list[asyncio.Future]
        ] = collections.defaultdict(list)

    async def wait_for_state(self, state: str) -> None:
        assert state in self.states

        future = asyncio.get_running_loop().create_future()
        self.futures_for_state[state].append(future)

        try:
            return await future
        finally:
            self.futures_for_state[state].remove(future)

    def set_state(self, state: str) -> None:
        assert state in self.states
        self.state = state

        for future in self.futures_for_state[state]:
            future.set_result(None)


class SerialProtocol(asyncio.Protocol):
    def __init__(self) -> None:
        self._buffer = bytearray()
        self._transport: serial_asyncio.SerialTransport | None = None
        self._connected_event = asyncio.Event()

    async def wait_until_connected(self) -> None:
        await self._connected_event.wait()

    def connection_made(self, transport: serial_asyncio.SerialTransport) -> None:
        _LOGGER.debug("Connection made: %s", transport)

        self._transport = transport
        self._connected_event.set()

    def send_data(self, data: bytes) -> None:
        data = bytes(data)
        _LOGGER.debug("Sending data %s", data)
        self._transport.write(data)

    def data_received(self, data: bytes) -> None:
        _LOGGER.debug("Received data %s", data)
        self._buffer += data

    def disconnect(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._buffer.clear()
            self._connected_event.clear()
