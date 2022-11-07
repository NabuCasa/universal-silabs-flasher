from __future__ import annotations

import sys
import time
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


def patch_pyserial() -> None:
    """Patch pyserial-asyncio's serial function to instead use the WebSerial transport."""
    serial_asyncio.create_serial_connection = create_serial_connection


SERIAL_PORT = None
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

    def __del__(self):
        self._cleanup(RuntimeError("Transport was not closed!"))

    def _cleanup(self, exception: BaseException | None) -> None:
        self._reader_task.cancel()
        self._writer_task.cancel()

        _LOGGER.debug("Releasing read lock...")
        if self._js_reader is not None:
            self._js_reader.releaseLock()
            self._js_reader = None

        _LOGGER.debug("Releasing write lock...")
        if self._js_writer is not None:
            self._js_writer.releaseLock()
            self._js_writer = None

        coroutine = self._async_cleanup(exception)
        _LOGGER.debug("Running coroutine: %r")
        task = asyncio.create_task(coroutine)

        while not task.done():
            _LOGGER.debug("Running coroutine: %r", task)
            time.sleep(0.01)

        _LOGGER.debug("Done with coroutine: %r")

        if self._protocol is not None:
            self._protocol.connection_lost(exception)
            self._protocol = None

    async def _async_cleanup(self, exception: BaseException | None) -> None:
        _LOGGER.debug("Closing port")
        await self._port.close()
        _LOGGER.debug("Port is closed")

    def close(self) -> None:
        _LOGGER.debug("Closing...")
        self._cleanup(None)
        _LOGGER.debug("Done closing")


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


async def create_serial_connection(
    loop: asyncio.BaseEventLoop,
    protocol_factory: typing.Callable[[], asyncio.Protocol],
    url: str,
    *,
    parity=None,
    stopbits=None,
    baudrate: int,
    rtscts=False,
) -> tuple[WebSerialTransport, asyncio.Protocol]:
    # `url` is ignored, `SERIAL_PORT` is used instead

    await SERIAL_PORT.open(
        baudRate=baudrate,
        flowControl="hardware" if rtscts else None,
    )

    protocol = protocol_factory()
    transport = WebSerialTransport(loop, protocol, SERIAL_PORT)

    return transport, protocol
