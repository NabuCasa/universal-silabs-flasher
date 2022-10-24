from __future__ import annotations

import typing
import asyncio
import logging
import functools
import collections

import serial_asyncio
from zigpy.ota.validators import parse_silabs_gbl

_LOGGER = logging.getLogger(__name__)


def crc16(data: bytes, polynomial: int) -> int:
    crc = 0x0000

    for c in data:
        crc ^= c << 8

        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) & 0xFFFF) ^ polynomial
            else:
                crc = (crc << 1) & 0xFFFF

    return crc


crc16_ccitt = functools.partial(crc16, polynomial=0x1021)


class BufferTooShort(Exception):
    pass


class StateMachine:
    def __init__(self, states: list[str], initial: str) -> None:
        assert initial in states

        self.states = states
        self.state = initial

        self.futures_for_state: typing.DefaultDict[
            str, list[asyncio.Future]
        ] = collections.defaultdict(list)

        self.callbacks_for_state: typing.DefaultDict[
            str, list[typing.Callable]
        ] = collections.defaultdict(list)

    async def wait_for_state(self, state: str) -> None:
        if self.state == state:
            return

        assert state in self.states

        future = asyncio.get_running_loop().create_future()
        self.futures_for_state[state].append(future)

        try:
            return await future
        finally:
            self.futures_for_state[state].remove(future)

    def add_callback_for_state(self, state: str, callback: typing.Callable) -> None:
        self.callbacks_for_state[state].append(callback)

    def remove_callback_for_state(self, state: str, callback: typing.Callable) -> None:
        self.callbacks_for_state[state].remove(callback)

    def set_state(self, state: str) -> None:
        assert state in self.states
        self.state = state

        for callback in self.callbacks_for_state[state]:
            callback()

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


def validate_silabs_gbl(data: bytes) -> None:
    for _tag, _value in parse_silabs_gbl(data):
        pass


def patch_pyserial_asyncio():
    def get_protocol(self) -> asyncio.Protocol:
        return self._protocol

    def set_protocol(self, protocol: asyncio.Protocol) -> None:
        self._protocol = protocol

    serial_asyncio.SerialTransport.get_protocol = get_protocol
    serial_asyncio.SerialTransport.set_protocol = set_protocol
