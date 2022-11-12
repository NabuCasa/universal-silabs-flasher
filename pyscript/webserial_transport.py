from __future__ import annotations

import sys
import typing
import asyncio
import logging

import js
import pyodide

# Patch some built-in modules so that pyserial imports
try:
    import fcntl
except ImportError:
    sys.modules["fcntl"] = object()

try:
    import termios
except ImportError:
    sys.modules["termios"] = object()

import serial_asyncio


async def make_coroutine(call):
    return await call


def patch_pyserial() -> None:
    """Patch pyserial-asyncio's serial function to instead use the WebSerial transport."""
    serial_asyncio.create_serial_connection = create_serial_connection


SERIAL_PORT = None
SERIAL_PORT_CLOSING_TASK: asyncio.Task | None = None

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
        self._protocol: asyncio.Protocol = protocol
        self._port = port

        self._write_queue = asyncio.Queue()
        self._is_closing = False

        self._js_reader = self._port.readable.getReader()
        self._js_writer = self._port.writable.getWriter()

        self._reader_task = loop.create_task(self._reader_loop())
        self._writer_task = loop.create_task(self._writer_loop())

        self._loop.call_soon(self._protocol.connection_made, self)

    async def _writer_loop(self):
        while True:
            chunk = await self._write_queue.get()

            try:
                await self._js_writer.write(js.Uint8Array.new(chunk))
            except Exception as e:
                self._cleanup(e)
                break

    async def _reader_loop(self):
        while True:
            result = await self._js_reader.read()
            if result.done:
                self._cleanup(RuntimeError("Other side has closed"))
                return

            self._protocol.data_received(bytes(result.value))

    def write(self, data) -> None:
        self._write_queue.put_nowait(data)

    def set_protocol(self, protocol: asyncio.Protocol) -> None:
        self._protocol = protocol

    def get_protocol(self) -> asyncio.Protocol:
        return self._protocol

    def is_closing(self) -> bool:
        return self._is_closing

    def __del__(self):
        self._cleanup(RuntimeError("Transport was not closed!"))

    def _cleanup(self, exception: BaseException | None) -> None:
        self._is_closing = True

        global SERIAL_PORT_CLOSING_TASK

        self._reader_task.cancel()
        self._writer_task.cancel()

        if self._js_reader is not None:
            self._js_reader.releaseLock()
            self._js_reader = None

        if self._js_writer is not None:
            self._js_writer.releaseLock()
            self._js_writer = None

        if self._port is not None:
            SERIAL_PORT_CLOSING_TASK = asyncio.create_task(
                make_coroutine(self._port.close())
            )
            self._port = None

        if self._protocol is not None:
            self._protocol.connection_lost(exception)
            self._protocol = None

    def close(self) -> None:
        self._cleanup(None)


async def prompt_serial_port(selector: str) -> pyodide.ffi.JsProxy:
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    element = js.document.querySelector(selector)

    async def callback(*args, **kwargs):
        port = await js.navigator.serial.requestPort()
        future.set_result(port)

        element.removeEventListener("click", callback_proxy)

    callback_proxy = pyodide.ffi.create_proxy(callback)
    element.addEventListener("click", callback_proxy)

    try:
        return await future
    finally:
        callback_proxy.destroy()


async def wait_file_upload(selector: str) -> bytes:
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    element = js.document.querySelector(selector)

    async def callback(event):
        files = list(event.target.files.to_py())
        js_data = js.Uint8Array.new(await files[0].arrayBuffer())
        data = bytearray(js_data)
        future.set_result(data)

        element.removeEventListener("change", callback_proxy)

    callback_proxy = pyodide.ffi.create_proxy(callback)
    element.addEventListener("change", callback_proxy)

    try:
        return await future
    finally:
        callback_proxy.destroy()


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
    global SERIAL_PORT_CLOSING_TASK

    # XXX: Since asyncio's `transport.close` is synchronous but JavaScript's is not, we
    # must delegate closing to a task and then "block" at the next asynchronous entry
    # point
    if SERIAL_PORT_CLOSING_TASK is not None:
        await SERIAL_PORT_CLOSING_TASK
        SERIAL_PORT_CLOSING_TASK = None

    # `url` is ignored, `SERIAL_PORT` is used instead
    await SERIAL_PORT.open(
        baudRate=baudrate,
        flowControl="hardware" if rtscts else None,
    )

    protocol = protocol_factory()
    transport = WebSerialTransport(loop, protocol, SERIAL_PORT)

    return transport, protocol
