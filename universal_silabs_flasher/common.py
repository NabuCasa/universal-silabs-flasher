from __future__ import annotations

import typing
import asyncio
import logging
import functools
import contextlib
import collections

import zigpy.serial
import async_timeout
import serial_asyncio
from zigpy.ota.validators import parse_silabs_gbl

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 1
PROBE_TIMEOUT = 2


def crc16(data: bytes, polynomial: int) -> int:
    """Calculate a CRC-16 checksum with a bit-packed polynomial."""
    crc = 0x0000

    for c in data:
        crc ^= c << 8

        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) & 0xFFFF) ^ polynomial
            else:
                crc = (crc << 1) & 0xFFFF

    return crc


# Used by both CPC and XModem
crc16_ccitt = functools.partial(crc16, polynomial=0x1021)


class BufferTooShort(Exception):
    """Protocol buffer requires more data to parse a packet."""


class StateMachine:
    """Asyncio-friendly state machine."""

    def __init__(self, states: set[str], initial: str) -> None:
        assert initial in states

        self.states = states
        self.state = initial

        self.futures_for_state: typing.DefaultDict[
            str, list[asyncio.Future]
        ] = collections.defaultdict(list)

    async def wait_for_state(self, state: str) -> None:
        """Waits for a state. Returns immediately if the state is active."""
        assert state in self.states

        if self.state == state:
            return

        future = asyncio.get_running_loop().create_future()
        self.futures_for_state[state].append(future)

        try:
            return await future
        finally:
            # Always clean up the future
            self.futures_for_state[state].remove(future)

    def set_state(self, state: str) -> None:
        assert state in self.states
        self.state = state

        for future in self.futures_for_state[state]:
            future.set_result(None)


class SerialProtocol(asyncio.Protocol):
    """Base class for packet-parsing serial protocol implementations."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._transport: serial_asyncio.SerialTransport | None = None
        self._connected_event = asyncio.Event()

    async def wait_until_connected(self) -> None:
        """Wait for the protocol's transport to be connected."""
        await self._connected_event.wait()

    def connection_made(self, transport: serial_asyncio.SerialTransport) -> None:
        _LOGGER.debug("Connection made: %s", transport)

        self._transport = transport
        self._connected_event.set()

    def send_data(self, data: bytes) -> None:
        """Sends data over the connected transport."""
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
    """Validates a Silicon Labs GBL firmware image structure and checksum."""
    for _tag, _value in parse_silabs_gbl(data):
        pass


def patch_pyserial_asyncio() -> None:
    """Patches pyserial-asyncio's `SerialTransport` to support swapping protocols."""

    if (
        serial_asyncio.SerialTransport.get_protocol
        is not asyncio.BaseTransport.get_protocol
    ):
        return

    def get_protocol(self) -> asyncio.Protocol:
        return self._protocol

    def set_protocol(self, protocol: asyncio.Protocol) -> None:
        self._protocol = protocol

    serial_asyncio.SerialTransport.get_protocol = get_protocol
    serial_asyncio.SerialTransport.set_protocol = set_protocol


@contextlib.asynccontextmanager
async def connect_protocol(port, baudrate, factory):
    loop = asyncio.get_running_loop()

    async with async_timeout.timeout(CONNECT_TIMEOUT):
        _, protocol = await zigpy.serial.create_serial_connection(
            loop=loop,
            protocol_factory=factory,
            url=port,
            baudrate=baudrate,
        )
        await protocol.wait_until_connected()

    try:
        yield protocol
    finally:
        protocol.disconnect()
