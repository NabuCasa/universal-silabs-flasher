from __future__ import annotations

import asyncio
import collections.abc
import logging
import sys
import typing

import js

# Patch some built-in modules so that pyserial imports
try:
    import fcntl  # noqa: F401
except ImportError:
    sys.modules["fcntl"] = object()  # type: ignore[assignment]

try:
    import termios  # noqa: F401
except ImportError:
    sys.modules["termios"] = object()  # type: ignore[assignment]

import serial_asyncio


async def make_coroutine(call: collections.abc.Awaitable) -> collections.abc.Coroutine:
    """Turns an awaitable into an actual coroutine."""
    return await call


def patch_pyserial() -> None:
    """Patch pyserial-asyncio to use a WebSerial transport."""
    serial_asyncio.create_serial_connection = create_serial_connection


_SERIAL_PORT = None
_SERIAL_PORT_CLOSING_TASK: asyncio.Task | None = None

_LOGGER = logging.getLogger(__name__)


class WebSerialTransport(asyncio.Transport):
    def __init__(
        self,
        loop: asyncio.BaseEventLoop,
        protocol: asyncio.Protocol,
        port,
    ) -> None:
        super().__init__()
        self._loop: asyncio.BaseEventLoop = loop
        self._protocol: asyncio.BaseProtocol | None = protocol
        self._port = port

        self._write_queue: asyncio.Queue = asyncio.Queue()
        self._is_closing = False

        self._js_reader = self._port.readable.getReader()
        self._js_writer = self._port.writable.getWriter()

        self._reader_task = loop.create_task(self._reader_loop())
        self._writer_task = loop.create_task(self._writer_loop())

        self._loop.call_soon(self._protocol.connection_made, self)

    async def _writer_loop(self) -> None:
        while True:
            chunk = await self._write_queue.get()

            try:
                await self._js_writer.write(js.Uint8Array.new(chunk))
            except Exception as e:
                self._cleanup(e)
                break

    async def _reader_loop(self) -> None:
        while True:
            result = await self._js_reader.read()
            if result.done:
                self._cleanup(RuntimeError("Other side has closed"))
                return

            self._protocol.data_received(bytes(result.value))

    def write(self, data: bytes) -> None:
        self._write_queue.put_nowait(data)

    def set_protocol(self, protocol: asyncio.BaseProtocol) -> None:
        self._protocol = protocol

    def get_protocol(self) -> asyncio.BaseProtocol:
        assert self._protocol is not None
        return self._protocol

    def is_closing(self) -> bool:
        return self._is_closing

    def __del__(self):
        self._cleanup(RuntimeError("Transport was not closed!"))

    def _cleanup(self, exception: BaseException | None) -> None:
        self._is_closing = True

        global _SERIAL_PORT_CLOSING_TASK

        self._reader_task.cancel()
        self._writer_task.cancel()

        if self._js_reader is not None:
            self._js_reader.releaseLock()
            self._js_reader = None

        if self._js_writer is not None:
            self._js_writer.releaseLock()
            self._js_writer = None

        if self._port is not None:
            _SERIAL_PORT_CLOSING_TASK = asyncio.create_task(
                make_coroutine(self._port.close())
            )
            self._port = None

        if self._protocol is not None:
            self._protocol.connection_lost(exception)
            self._protocol = None

    def close(self) -> None:
        self._cleanup(None)


def set_global_serial_port(serial_port) -> None:
    global _SERIAL_PORT
    _SERIAL_PORT = serial_port


async def create_serial_connection(
    loop: asyncio.BaseEventLoop,
    protocol_factory: typing.Callable[[], asyncio.Protocol],
    url: str,
    *,
    parity=None,
    stopbits=None,
    baudrate: int,
    rtscts=False,
    xonxoff=False,
) -> tuple[WebSerialTransport, asyncio.Protocol]:
    global _SERIAL_PORT_CLOSING_TASK

    # XXX: Since asyncio's `transport.close` is synchronous but JavaScript's is not, we
    # must delegate closing to a task and then "block" at the next asynchronous entry
    # point
    if _SERIAL_PORT_CLOSING_TASK is not None:
        await _SERIAL_PORT_CLOSING_TASK
        _SERIAL_PORT_CLOSING_TASK = None

    # `url` is ignored, `_SERIAL_PORT` is used instead
    await _SERIAL_PORT.open(
        baudRate=baudrate,
        flowControl="hardware" if rtscts else None,
    )

    protocol = protocol_factory()
    transport = WebSerialTransport(loop, protocol, _SERIAL_PORT)

    return transport, protocol
