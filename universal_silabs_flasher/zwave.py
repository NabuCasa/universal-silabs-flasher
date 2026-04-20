from __future__ import annotations

import asyncio
import dataclasses
import logging
import typing
from typing import Self

from zigpy.serial import SerialProtocol
import zigpy.types as t

from .common import BufferTooShort, Version

_LOGGER = logging.getLogger(__name__)

COMMAND_TIMEOUT = 2


class MessageType(t.enum8):
    REQUEST = 0x00
    RESPONSE = 0x01


class FunctionID(t.enum8):
    SERIAL_API_GET_CAPABILITIES = 0x07
    SERIAL_API_ENTER_BOOTLOADER = 0x27


SOF = 0x01
ACK = 0x06
NAK = 0x15
CAN = 0x18


@dataclasses.dataclass(frozen=True)
class ZWaveFrame:
    type: MessageType
    function_id: FunctionID
    data: bytes

    @classmethod
    def deserialize(cls, data: bytes | bytearray) -> tuple[Self, bytes | bytearray]:
        if len(data) < 2:
            raise BufferTooShort()

        if data[0] != SOF:
            raise ValueError(f"Expected SOF, got 0x{data[0]:02x}")

        length = data[1]

        if len(data) < 2 + length:
            raise BufferTooShort()

        expected_checksum = data[1 + length]
        computed_checksum = 0xFF

        for b in data[1 : 1 + length]:
            computed_checksum ^= b

        if computed_checksum != expected_checksum:
            raise ValueError(
                f"Checksum mismatch: expected 0x{computed_checksum:02x},"
                f" got 0x{expected_checksum:02x}"
            )

        msg_type = MessageType(data[2])  # type: ignore[no-untyped-call]
        function_id = FunctionID(data[3])  # type: ignore[no-untyped-call]
        payload = bytes(data[4 : 1 + length])

        return (
            cls(type=msg_type, function_id=function_id, data=payload),
            data[2 + length :],
        )

    def serialize(self) -> bytes:
        length = 1 + 1 + len(self.data) + 1
        data = (
            bytes([length])
            + self.type.serialize()
            + self.function_id.serialize()
            + self.data
        )

        checksum = 0xFF
        for b in data:
            checksum ^= b

        return bytes([SOF]) + data + bytes([checksum])


class ZWaveProtocol(SerialProtocol):
    _buffer: bytearray

    def __init__(self) -> None:
        super().__init__()
        self._pending_frames: dict[FunctionID, asyncio.Future[ZWaveFrame]] = {}

    def send_data(self, data: bytes) -> None:
        assert self._transport is not None
        _LOGGER.debug("Sending data %s", data)
        self._transport.write(data)

    def data_received(self, data: bytes) -> None:
        super().data_received(data)

        while self._buffer:
            byte = self._buffer[0]

            if byte == ACK:
                _LOGGER.debug("Received ACK")
                self._buffer = self._buffer[1:]
                continue

            if byte in (NAK, CAN):
                _LOGGER.debug("Received 0x%02x", byte)
                self._buffer = self._buffer[1:]
                continue

            if byte != SOF:
                _LOGGER.debug("Discarding unexpected byte 0x%02x", byte)
                self._buffer = self._buffer[1:]
                continue

            try:
                frame, new_buffer = ZWaveFrame.deserialize(self._buffer)
            except BufferTooShort:
                break
            except ValueError as e:
                _LOGGER.debug("Failed to parse buffer %r: %r", self._buffer, e)
                self._buffer = self._buffer[1:]
            else:
                self._buffer = typing.cast(bytearray, new_buffer)
                self.send_data(bytes([ACK]))
                self.frame_received(frame)

    def frame_received(self, frame: ZWaveFrame) -> None:
        _LOGGER.debug("Received frame %r", frame)

        if frame.type != MessageType.RESPONSE:
            return

        if frame.function_id not in self._pending_frames:
            _LOGGER.debug("Received unsolicited response for %r", frame.function_id)
            return

        future = self._pending_frames[frame.function_id]

        if future.done():
            _LOGGER.debug("Ignoring duplicate response for %r", frame.function_id)
            return

        future.set_result(frame)

    async def send_command(
        self,
        function_id: FunctionID,
        data: bytes = b"",
        *,
        retries: int = 2,
        timeout: float = COMMAND_TIMEOUT,
        retry_delay: float = 0.1,
    ) -> ZWaveFrame:
        assert function_id not in self._pending_frames

        future: asyncio.Future[ZWaveFrame] = asyncio.get_running_loop().create_future()
        self._pending_frames[function_id] = future

        try:
            for attempt in range(retries + 1):
                frame = ZWaveFrame(
                    type=MessageType.REQUEST, function_id=function_id, data=data
                )

                _LOGGER.debug("Sending frame %r", frame)
                self.send_data(frame.serialize())

                try:
                    async with asyncio.timeout(timeout):
                        return await future
                except TimeoutError:
                    _LOGGER.debug(
                        "Failed to send %r, trying again in %0.2fs (attempt %s of %s)",
                        frame,
                        retry_delay,
                        attempt + 1,
                        retries + 1,
                    )

                    if attempt >= retries:
                        raise

                    await asyncio.sleep(retry_delay)
        finally:
            self._pending_frames.pop(function_id, None)

        # Unreachable
        assert False

    async def probe(self) -> Version:
        rsp = await self.send_command(FunctionID.SERIAL_API_GET_CAPABILITIES)

        major = rsp.data[0]
        minor = rsp.data[1]

        return Version(f"{major}.{minor}")

    async def enter_bootloader(self) -> None:
        """Reboot the device into the bootloader. No response is expected."""
        frame = ZWaveFrame(
            type=MessageType.REQUEST,
            function_id=FunctionID.SERIAL_API_ENTER_BOOTLOADER,
            data=b"",
        )
        self.send_data(frame.serialize())
