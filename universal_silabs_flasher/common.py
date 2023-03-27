from __future__ import annotations

import typing
import asyncio
import logging
import contextlib
import collections

import crc
import click
import zigpy.serial
import async_timeout
import serial_asyncio

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 1
PROBE_TIMEOUT = 2


CRC_CCITT = crc.Calculator(
    crc.Configuration(
        width=16,
        polynomial=0x1021,
        init_value=0x0000,
        final_xor_value=0x0000,
        reverse_input=False,
        reverse_output=False,
    )
)

CRC_KERMIT = crc.Calculator(
    crc.Configuration(
        width=16,
        polynomial=0x1021,
        init_value=0xFFFF,
        final_xor_value=0xFFFF,
        reverse_input=True,
        reverse_output=True,
    )
)


# Used by both CPC and XModem
def crc16_ccitt(data: bytes) -> int:
    return CRC_CCITT.checksum(data)


# Used by HDLC-Lite
def crc16_kermit(data: bytes) -> int:
    return CRC_KERMIT.checksum(data)


class BufferTooShort(Exception):
    """Protocol buffer requires more data to parse a packet."""


class StateMachine:
    """Asyncio-friendly state machine."""

    def __init__(self, states: set[str], initial: str) -> None:
        if initial not in states:
            raise ValueError(f"Unknown initial state {initial!r}: expected {states!r}")

        self._states = states
        self._state = initial

        self._futures_for_state: typing.DefaultDict[
            str, list[asyncio.Future]
        ] = collections.defaultdict(list)

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, state: str) -> None:
        if state not in self._states:
            raise ValueError(f"Unknown state {state!r}: expected {self._states!r}")

        self._state = state

        for future in self._futures_for_state[state]:
            future.set_result(None)

    async def wait_for_state(self, state: str) -> None:
        """Waits for a state. Returns immediately if the state is active."""
        assert state in self._states

        if self.state == state:
            return

        future = asyncio.get_running_loop().create_future()
        self._futures_for_state[state].append(future)

        try:
            return await future
        finally:
            # Always clean up the future
            self._futures_for_state[state].remove(future)


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

        # Required for Windows to be able to re-connect to the same serial port
        await asyncio.sleep(0)


class CommaSeparatedNumbers(click.ParamType):
    """Click type to parse comma-separated numbers into a list of integers."""

    name = "numbers"

    def type_cast_value(self, ctx: click.Context, value: str) -> list[int]:
        values = []

        for v in value.split(","):
            if not v.strip():
                continue

            try:
                values.append(int(v, 10))
            except ValueError:
                raise click.BadParameter(
                    f"Comma-separated list of numbers contains bad value: {v!r}"
                )

        return values


def put_first(lst: list[typing.Any], elements: list[typing.Any]) -> list[typing.Any]:
    """Orders a list so that the provided element is first."""
    return elements + [e for e in lst if e not in elements]
