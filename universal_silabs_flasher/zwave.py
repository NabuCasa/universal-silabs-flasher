from __future__ import annotations

import asyncio
import dataclasses
import logging
import typing

from zigpy.serial import SerialProtocol

from .common import BufferTooShort, Version, asyncio_timeout
from .zwave_types import FunctionID, MessageType

_LOGGER = logging.getLogger(__name__)

SOF = 0x01
ACK = 0x06
NAK = 0x15
CAN = 0x18

COMMAND_TIMEOUT = 2


@dataclasses.dataclass(frozen=True)
class ZWaveFrame:
    type: MessageType
    function_id: FunctionID
    data: bytes

    @classmethod
    def deserialize(cls, data: bytes) -> tuple[ZWaveFrame, bytes]:
        if len(data) < 2:
            raise BufferTooShort()

        if data[0] != SOF:
            raise ValueError(f"Expected SOF, got 0x{data[0]:02x}")

        length = data[1]

        if len(data) < 2 + length:
            raise BufferTooShort()

        # Checksum covers [length, type, function_id, payload...] (data[1:1+length])
        checksum_input = data[1 : 1 + length]
        received_checksum = data[1 + length]

        computed_checksum = 0xFF
        for b in checksum_input:
            computed_checksum ^= b

        if computed_checksum != received_checksum:
            raise ValueError(
                f"Checksum mismatch: expected 0x{computed_checksum:02x},"
                f" got 0x{received_checksum:02x}"
            )

        msg_type = MessageType(data[2])
        function_id = FunctionID(data[3])
        payload = bytes(data[4 : 1 + length])

        return (
            cls(type=msg_type, function_id=function_id, data=payload),
            data[2 + length :],
        )

    def serialize(self) -> bytes:
        length = 1 + 1 + len(self.data) + 1

        body = (
            bytes([length])
            + self.type.serialize()
            + self.function_id.serialize()
            + self.data
        )

        checksum = 0xFF
        for b in body:
            checksum ^= b

        return bytes([SOF]) + body + bytes([checksum])


class ZWaveProtocol(SerialProtocol):
    _buffer: bytearray

    def __init__(self) -> None:
        super().__init__()
        self._pending_frames: dict[FunctionID, asyncio.Future] = {}

    def send_data(self, data: bytes) -> None:
        assert self._transport is not None
        _LOGGER.debug("Sending data %s", data)
        self._transport.write(data)

    def data_received(self, data: bytes) -> None:
        super().data_received(data)

        while self._buffer:
            first_byte = self._buffer[0]

            if first_byte == ACK:
                _LOGGER.debug("Received ACK")
                self._buffer = self._buffer[1:]
                continue

            if first_byte in (NAK, CAN):
                _LOGGER.debug("Received 0x%02x", first_byte)
                self._buffer = self._buffer[1:]
                continue

            if first_byte != SOF:
                _LOGGER.debug("Discarding unexpected byte 0x%02x", first_byte)
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
                if self._transport is not None:
                    self._transport.write(bytes([ACK]))
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
        frame = ZWaveFrame(type=MessageType.REQUEST, function_id=function_id, data=data)

        assert function_id not in self._pending_frames

        future = asyncio.get_running_loop().create_future()
        self._pending_frames[function_id] = future

        try:
            for attempt in range(retries + 1):
                _LOGGER.debug("Sending frame %r", frame)
                self.send_data(frame.serialize())

                try:
                    async with asyncio_timeout(timeout):
                        return await asyncio.shield(future)
                except asyncio.TimeoutError:
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

        raise AssertionError("Unreachable")

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
