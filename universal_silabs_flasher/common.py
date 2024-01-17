from __future__ import annotations

import asyncio
import collections
import contextlib
import dataclasses
import functools
import logging
import re
import typing

import async_timeout
import click
import crc
import serial_asyncio
import zigpy.serial

if typing.TYPE_CHECKING:
    from typing_extensions import Self

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


def pad_to_multiple(data: bytes, multiple: int, padding: bytes) -> bytes:
    assert len(padding) == 1

    if len(data) % multiple == 0:
        return data

    num_complete_blocks = len(data) // multiple
    padded_size = multiple * (num_complete_blocks + 1)

    return data + padding * (padded_size - len(data))


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
        assert self._transport is not None
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

    def convert(
        self, value: typing.Any, param: click.Parameter | None, ctx: click.Context
    ) -> list[int]:
        if isinstance(value, list):
            return value

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


@dataclasses.dataclass(frozen=True, order=True)
class VersionComponent:
    comparable: bool
    data: str | int


@functools.total_ordering
class Version:
    _SEPARATORS = {".", "-", "/", "_", " build "}
    _SEPARATORS_REGEX = re.compile(
        "(" + "|".join(re.escape(s) for s in _SEPARATORS) + ")"
    )

    def __init__(self, version: str) -> None:
        self.components: list[VersionComponent] = []
        # 2.00.01
        # 7.2.2.0 build 190
        # 4.2.2
        # SL-OPENTHREAD/2.2.2.0_GitHub-91fa1f455
        # 4.4.0-2546d625-dirty-676fdb09
        for component in self._SEPARATORS_REGEX.split(version):
            if component.isdigit():
                self.components.append(
                    VersionComponent(comparable=True, data=int(component))
                )
            else:
                self.components.append(
                    VersionComponent(comparable=False, data=component)
                )

    def comparable_components(self) -> tuple[VersionComponent, ...]:
        return tuple(c for c in self.components if c.comparable)

    def compatible_with(self, other: Self) -> bool:
        our_comparable = self.comparable_components()
        their_comparable = other.comparable_components()

        prefix_length = min(len(our_comparable), len(their_comparable))
        return our_comparable[:prefix_length] == their_comparable[:prefix_length]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented

        return self.components == other.components

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented

        our_comparable = self.comparable_components()
        their_comparable = other.comparable_components()

        return our_comparable < their_comparable

    def __repr__(self) -> str:
        concatenated = "".join(str(c.data) for c in self.components)
        comparable = ".".join(str(c.data) for c in self.comparable_components())

        if concatenated == comparable:
            return f"{concatenated!r}"

        return f"{concatenated!r} ({comparable})"
