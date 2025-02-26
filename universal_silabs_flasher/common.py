from __future__ import annotations

import asyncio
import collections
import contextlib
import dataclasses
import functools
import logging
import re
import sys
import typing

import click
import crc
import zigpy.serial

if sys.version_info[:2] < (3, 11):
    from async_timeout import timeout as asyncio_timeout  # pragma: no cover
else:
    from asyncio import timeout as asyncio_timeout  # pragma: no cover

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

        self._futures_for_state: collections.defaultdict[str, list[asyncio.Future]] = (
            collections.defaultdict(list)
        )

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

    def cancel_all_futures(self, exc: BaseException) -> None:
        for futures in self._futures_for_state.values():
            for future in futures:
                future.set_exception(exc)

    async def wait_for_state(self, state: str, timeout: float = 30.0) -> None:
        """Waits for a state. Returns immediately if the state is active."""
        assert state in self._states

        if self.state == state:
            return

        future = asyncio.get_running_loop().create_future()
        self._futures_for_state[state].append(future)

        try:
            async with asyncio_timeout(timeout):
                return await future
        finally:
            # Always clean up the future
            self._futures_for_state[state].remove(future)


@contextlib.asynccontextmanager
async def connect_protocol(port, baudrate, factory):
    loop = asyncio.get_running_loop()

    async with asyncio_timeout(CONNECT_TIMEOUT):
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
        await protocol.disconnect()


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
    _SEPARATORS = {".", "-", "/", "_", " build ", " GA build "}
    _SEPARATORS_REGEX = re.compile(
        "(" + "|".join(re.escape(s) for s in _SEPARATORS) + ")"
    )

    def __init__(self, version: str) -> None:
        self.orig_version = version
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


class FlowControlSerialProtocol(zigpy.serial.SerialProtocol):
    def _set_signals(
        self,
        *,
        rts: bool | None = None,
        cts: bool | None = None,
        dtr: bool | None = None,
    ) -> None:
        if rts is not None:
            self._transport.serial.rts = rts

        if cts is not None:
            self._transport.serial.cts = cts

        if dtr is not None:
            self._transport.serial.dtr = dtr

    async def set_signals(
        self,
        *,
        rts: bool | None = None,
        cts: bool | None = None,
        dtr: bool | None = None,
    ) -> None:
        _LOGGER.debug(
            "Setting UART signals: rts=%s, cts=%s, dtr=%s",
            int(rts) if rts is not None else "-",
            int(cts) if cts is not None else "-",
            int(dtr) if dtr is not None else "-",
        )

        if hasattr(self._transport, "set_signals"):
            await self._transport.set_signals(rts=rts, cts=cts, dtr=dtr)
            return

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: self._set_signals(rts=rts, cts=cts, dtr=dtr)
        )
