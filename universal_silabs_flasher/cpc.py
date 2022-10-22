from __future__ import annotations

import typing
import asyncio
import logging
import functools
import dataclasses

import zigpy.types
import async_timeout

from . import cpc_types
from .common import SerialProtocol

_LOGGER = logging.getLogger(__name__)


class BufferTooShort(Exception):
    pass


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


def extract_frame(cpc_frame: CPCFrame):
    if cpc_frame.frame_type() != cpc_types.FrameType.UNNUMBERED:
        raise ValueError("Unsupported frame type")

    return UnnumberedFrame.from_bytes(cpc_frame.payload)


@dataclasses.dataclass(frozen=True)
class PropertyCommand:
    property_id: cpc_types.PropertyId
    value: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> PropertyCommand:
        property_id, data = cpc_types.PropertyId.deserialize(data)

        return cls(
            property_id=property_id,
            value=data,
        )

    def to_bytes(self) -> bytes:
        return self.property_id.serialize() + self.value


@dataclasses.dataclass(frozen=True)
class ResetCommand:
    status: cpc_types.Status | None

    @classmethod
    def from_bytes(cls, data: bytes) -> ResetCommand:
        if not data:
            status = None
        else:
            status, rest = cpc_types.Status.deserialize(data)
            assert not rest

        return cls(status=status)

    def to_bytes(self) -> bytes:
        return self.status.serialize() if self.status is not None else b""


@dataclasses.dataclass(frozen=True)
class UnnumberedFrame:
    command_id: cpc_types.UnnumberedFrameCommandId
    command_seq: zigpy.types.uint8_t
    length: zigpy.types.uint16_t = dataclasses.field(repr=False)
    payload: bytes

    _COMMANDS = {
        cpc_types.UnnumberedFrameCommandId.PROP_VALUE_GET: PropertyCommand,
        cpc_types.UnnumberedFrameCommandId.PROP_VALUE_SET: PropertyCommand,
        cpc_types.UnnumberedFrameCommandId.PROP_VALUE_IS: PropertyCommand,
        cpc_types.UnnumberedFrameCommandId.RESET: ResetCommand,
    }

    @classmethod
    def from_bytes(cls, data: bytes) -> UnnumberedFrame:
        command_id, data = cpc_types.UnnumberedFrameCommandId.deserialize(data)
        command_seq, data = zigpy.types.uint8_t.deserialize(data)
        length, data = zigpy.types.uint16_t.deserialize(data)

        if len(data) < length:
            raise ValueError("Frame is too short")

        payload, data = data[:length], data[length:]

        if data:
            raise ValueError("Trailing data in frame")

        return cls(
            command_id=command_id,
            command_seq=command_seq,
            length=None,
            payload=cls._COMMANDS[command_id].from_bytes(payload),
        )

    def to_bytes(self) -> bytes:
        payload = self.payload.to_bytes()

        return (
            self.command_id.serialize()
            + self.command_seq.serialize()
            + zigpy.types.uint16_t(len(payload)).serialize()
            + payload
        )


@dataclasses.dataclass(frozen=True)
class CPCFrame:
    flag: zigpy.types.uint8_t = dataclasses.field(repr=False)
    endpoint: cpc_types.EndpointId
    length: zigpy.types.uint16_t = dataclasses.field(repr=False)
    control: zigpy.types.uint8_t
    header_checksum: zigpy.types.uint16_t = dataclasses.field(repr=False)

    payload: bytes
    payload_checksum: zigpy.types.uint16_t = dataclasses.field(repr=False)

    def serialize(self) -> bytes:
        payload = self.payload.to_bytes()
        length = zigpy.types.uint16_t(len(payload) + 2)

        header = (
            self.flag.serialize()
            + self.endpoint.serialize()
            + length.serialize()
            + self.control.serialize()
        )

        header_checksum = crc16_ccitt(header).to_bytes(2, "little")
        payload_checksum = crc16_ccitt(payload).to_bytes(2, "little")

        return header + header_checksum + payload + payload_checksum

    @classmethod
    def deserialize(cls, data: bytes) -> tuple[CPCFrame, bytes]:
        if len(data) < 7:
            raise BufferTooShort("Data is too short to contain packet header")

        orig_data = data
        flag, data = zigpy.types.uint8_t.deserialize(data)

        if flag != cpc_types.FLAG:
            raise ValueError("Invalid flag")

        endpoint, data = cpc_types.EndpointId.deserialize(data)
        length, data = zigpy.types.uint16_t.deserialize(data)
        control, data = zigpy.types.uint8_t.deserialize(data)
        header_checksum, data = zigpy.types.uint16_t.deserialize(data)

        if crc16_ccitt(orig_data[:5]) != header_checksum:
            raise ValueError("Invalid header checksum")

        if len(data) < length:
            raise BufferTooShort("Data is too short to contain packet payload")

        payload, data = data[: length - 2], data[length - 2 :]
        payload_checksum, data = zigpy.types.uint16_t.deserialize(data)

        if crc16_ccitt(payload) != payload_checksum:
            raise ValueError("Invalid payload checksum")

        frame = cls(
            flag=flag,
            endpoint=endpoint,
            length=length,
            control=control,
            header_checksum=header_checksum,
            payload=payload,
            payload_checksum=payload_checksum,
        )

        frame_with_parsed_payload = dataclasses.replace(
            frame, payload=extract_frame(frame)
        )

        return frame_with_parsed_payload, data

    def frame_type(self):
        frame_type = (self.control & 0b11000000) >> 6

        if frame_type == 0:
            frame_type = 1

        return cpc_types.FrameType(frame_type)

    def seq(self):
        return (self.control & 0b01110000) >> 4

    def supervisory_function(self):
        assert self.frame_type() == cpc_types.FrameType.SUPERVISORY
        return (self.control & 0b00110000) >> 4

    def ack(self):
        return (self.control & 0b00000111) >> 0

    def unnumbered_type(self) -> cpc_types.UnnumberedFrameType:
        assert self.frame_type() == cpc_types.FrameType.UNNUMBERED
        return cpc_types.UnnumberedFrameType((self.control & 0b00111111) >> 0)

    def poll_final(self):
        return (self.control & 0b00001000) >> 3


class CPCProtocol(SerialProtocol):
    def __init__(self) -> None:
        super().__init__()
        self._command_seq = 0
        self._pending_frames: dict[int, asyncio.Future] = {}

    async def get_secondary_version(self) -> str:
        rsp = await self.send_unnumbered_frame(
            command_id=cpc_types.UnnumberedFrameCommandId.PROP_VALUE_GET,
            command_payload=PropertyCommand(
                property_id=cpc_types.PropertyId.SECONDARY_APP_VERSION,
                value=b"",
            ),
            retries=3,
        )

        version_bytes = rsp.payload.payload.value

        return version_bytes.split(b"\x00", 1)[0].decode("ascii")

    async def probe(self):
        return await self.get_secondary_version()

    def data_received(self, data: bytes) -> None:
        super().data_received(data)

        while self._buffer:
            try:
                frame, new_buffer = CPCFrame.deserialize(self._buffer)
                self._buffer = typing.cast(bytearray, new_buffer)
            except BufferTooShort:
                break
            except ValueError as e:
                self._buffer = self._buffer[
                    self._buffer.find(bytes([cpc_types.FLAG])) :
                ]
                _LOGGER.warning("Failed to parse buffer %r: %r", self._buffer, e)
            else:
                self.frame_received(frame)

    def frame_received(self, frame: CPCFrame) -> None:
        _LOGGER.info("Parsed frame %s %s", frame.unnumbered_type(), frame)

        if frame.unnumbered_type() == cpc_types.UnnumberedFrameType.POLL_FINAL:
            if frame.payload.command_seq not in self._pending_frames:
                _LOGGER.warning("Received an unsolicited frame: %s", frame)
                return

            future = self._pending_frames.pop(frame.payload.command_seq)
            future.set_result(frame)

    async def send_unnumbered_frame(
        self, command_id, command_payload, *, retries=3, timeout=1, retry_delay=0.1
    ):
        frame = CPCFrame(
            flag=cpc_types.FLAG,
            endpoint=cpc_types.EndpointId.SYSTEM,
            control=zigpy.types.uint8_t(
                (cpc_types.FrameType.UNNUMBERED << 6)
                | (cpc_types.UnnumberedFrameType.POLL_FINAL << 0)
            ),
            payload=UnnumberedFrame(
                command_id=command_id,
                command_seq=zigpy.types.uint8_t(self._command_seq),
                length=None,
                payload=command_payload,
            ),
            length=None,
            header_checksum=None,
            payload_checksum=None,
        )
        self._command_seq = (self._command_seq + 1) & 0xFF

        assert self._command_seq not in self._pending_frames

        future = asyncio.get_running_loop().create_future()
        self._pending_frames[frame.payload.command_seq] = future

        try:
            for attempt in range(retries + 1):
                _LOGGER.info("Sending frame %s", frame)
                self.send_data(frame.serialize())

                try:
                    async with async_timeout.timeout(timeout):
                        return await asyncio.shield(future)
                except asyncio.TimeoutError:
                    _LOGGER.warning(
                        "Failed to send %s, trying again in %0.2fs (attempt %s of %s)",
                        frame,
                        retry_delay,
                        attempt + 1,
                        retries + 1,
                    )

                    if attempt == retries:
                        raise

                    await asyncio.sleep(retry_delay)
        finally:
            self._pending_frames.pop(frame.payload.command_seq, None)
