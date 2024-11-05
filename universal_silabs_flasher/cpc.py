from __future__ import annotations

import asyncio
import dataclasses
import logging
import typing

from zigpy.serial import SerialProtocol
import zigpy.types

from . import cpc_types
from .common import BufferTooShort, Version, asyncio_timeout, crc16_ccitt

_LOGGER = logging.getLogger(__name__)


def parse_subframe(cpc_frame: CPCTransportFrame) -> UnnumberedFrame:
    """Parses a CPC sub-frame from a CPC frame. Only `UnnumberedFrame` is supported."""
    frame_type = cpc_frame.frame_type()

    if frame_type != cpc_types.FrameType.UNNUMBERED:
        raise ValueError(f"Unsupported frame type: {frame_type!r}")

    return UnnumberedFrame.from_bytes(cpc_frame.payload)


class Command:
    """Base class for unnumbered commands."""


@dataclasses.dataclass(frozen=True)
class PropertyCommand(Command):
    """Unnumbered frame command to get/set/read a property."""

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
class ResetCommand(Command):
    """Unnumbered frame command to reset the device."""

    # The `status` field is set when the device responds
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
    """Unnumbered CPC frame"""

    command_id: cpc_types.UnnumberedFrameCommandId
    command_seq: zigpy.types.uint8_t
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
class CPCTransportFrame:
    """CPC transport frame"""

    endpoint: cpc_types.EndpointId
    control: zigpy.types.uint8_t
    payload: bytes

    def serialize(self) -> bytes:
        """Serialize the transport frame and compute lengths and checksums."""
        payload = self.payload.to_bytes()
        length = zigpy.types.uint16_t(len(payload) + 2)

        header = (
            cpc_types.FLAG.serialize()
            + self.endpoint.serialize()
            + length.serialize()
            + self.control.serialize()
        )

        header_checksum = crc16_ccitt(header).to_bytes(2, "little")
        payload_checksum = crc16_ccitt(payload).to_bytes(2, "little")

        return header + header_checksum + payload + payload_checksum

    @classmethod
    def deserialize(cls, data: bytes) -> tuple[CPCTransportFrame, bytes]:
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
            endpoint=endpoint,
            control=control,
            payload=payload,
        )

        frame_with_parsed_payload = dataclasses.replace(
            frame, payload=parse_subframe(frame)
        )

        return frame_with_parsed_payload, data

    def frame_type(self) -> cpc_types.FrameType:
        frame_type = (self.control & 0b11000000) >> 6

        if frame_type == 0:
            frame_type = 1

        return cpc_types.FrameType(frame_type)

    def seq(self) -> int:
        return (self.control & 0b01110000) >> 4

    def supervisory_function(self) -> int:
        assert self.frame_type() == cpc_types.FrameType.SUPERVISORY
        return (self.control & 0b00110000) >> 4

    def ack(self) -> int:
        return (self.control & 0b00000111) >> 0

    def unnumbered_type(self) -> cpc_types.UnnumberedFrameType:
        assert self.frame_type() == cpc_types.FrameType.UNNUMBERED
        return cpc_types.UnnumberedFrameType((self.control & 0b00111111) >> 0)

    def poll_final(self) -> bool:
        return bool((self.control & 0b00001000) >> 3)


class CPCProtocol(SerialProtocol):
    """Partial implementation of the CPC protocol."""

    _buffer: bytearray

    def __init__(self) -> None:
        super().__init__()
        self._command_seq: int = 0
        self._pending_frames: dict[int, asyncio.Future] = {}

    async def probe(self) -> Version:
        cpc_version = await self.get_cpc_version()
        secondary_version = await self.get_secondary_version()

        # Prefer the secondary version if possible, on newer firmwares we customize it
        if secondary_version is not None:
            return secondary_version

        return cpc_version

    async def enter_bootloader(self) -> None:
        """Reboot into the bootloader."""
        await self.send_unnumbered_frame(
            command_id=cpc_types.UnnumberedFrameCommandId.PROP_VALUE_SET,
            command_payload=PropertyCommand(
                property_id=cpc_types.PropertyId.BOOTLOADER_REBOOT_MODE,
                value=cpc_types.RebootMode.BOOTLOADER.serialize(),
            ),
        )

        await self.send_unnumbered_frame(
            command_id=cpc_types.UnnumberedFrameCommandId.RESET,
            command_payload=ResetCommand(status=None),
        )

        # A small delay is necessary when switching baudrates
        await asyncio.sleep(0.5)

    async def get_cpc_version(self) -> Version:
        """Read the secondary CPC version from the device."""
        rsp = await self.send_unnumbered_frame(
            command_id=cpc_types.UnnumberedFrameCommandId.PROP_VALUE_GET,
            command_payload=PropertyCommand(
                property_id=cpc_types.PropertyId.SECONDARY_CPC_VERSION,
                value=b"",
            ),
            retries=3,
        )

        version_bytes = rsp.payload.payload.value
        major, version_bytes = zigpy.types.uint32_t.deserialize(version_bytes)
        minor, version_bytes = zigpy.types.uint32_t.deserialize(version_bytes)
        patch, version_bytes = zigpy.types.uint32_t.deserialize(version_bytes)
        assert not version_bytes

        return Version(f"{major}.{minor}.{patch}")

    async def get_secondary_version(self) -> Version | None:
        """Read the secondary app version from the device."""
        rsp = await self.send_unnumbered_frame(
            command_id=cpc_types.UnnumberedFrameCommandId.PROP_VALUE_GET,
            command_payload=PropertyCommand(
                property_id=cpc_types.PropertyId.SECONDARY_APP_VERSION,
                value=b"",
            ),
            retries=3,
        )

        version_bytes = rsp.payload.payload.value

        if version_bytes == b"UNDEFINED\x00":
            return None

        return Version(version_bytes.split(b"\x00", 1)[0].decode("ascii"))

    def send_data(self, data: bytes) -> None:
        assert self._transport is not None
        _LOGGER.debug("Sending data %s", data)
        self._transport.write(data)

    def data_received(self, data: bytes) -> None:
        super().data_received(data)

        while self._buffer:
            try:
                frame, new_buffer = CPCTransportFrame.deserialize(self._buffer)
                self._buffer = typing.cast(bytearray, new_buffer)
            except BufferTooShort:
                break
            except ValueError as e:
                _LOGGER.debug("Failed to parse buffer %r: %r", self._buffer, e)
                flag = bytes([cpc_types.FLAG])

                try:
                    self._buffer = self._buffer[self._buffer.index(flag) :]
                except ValueError:
                    self._buffer.clear()
                    break
            else:
                self.frame_received(frame)

    def frame_received(self, frame: CPCTransportFrame) -> None:
        _LOGGER.debug("Parsed frame %s %s", frame.unnumbered_type(), frame)

        if frame.unnumbered_type() == cpc_types.UnnumberedFrameType.POLL_FINAL:
            if frame.payload.command_seq not in self._pending_frames:
                _LOGGER.debug("Received an unsolicited frame: %s", frame)
                return

            future = self._pending_frames.pop(frame.payload.command_seq)
            future.set_result(frame)

    async def send_unnumbered_frame(
        self,
        command_id: cpc_types.UnnumberedFrameCommandId,
        command_payload: Command,
        *,
        retries: int = 3,
        timeout: float = 1,
        retry_delay: float = 0.1,
    ) -> Command:
        """Send an unnumbered frame to the device and return the response."""
        frame = CPCTransportFrame(
            endpoint=cpc_types.EndpointId.SYSTEM,
            control=zigpy.types.uint8_t(
                (cpc_types.FrameType.UNNUMBERED << 6)
                | (cpc_types.UnnumberedFrameType.POLL_FINAL << 0)
            ),
            payload=UnnumberedFrame(
                command_id=command_id,
                command_seq=zigpy.types.uint8_t(self._command_seq),
                payload=command_payload,
            ),
        )
        self._command_seq = (self._command_seq + 1) & 0xFF

        assert self._command_seq not in self._pending_frames

        future = asyncio.get_running_loop().create_future()
        self._pending_frames[frame.payload.command_seq] = future

        try:
            for attempt in range(retries + 1):
                _LOGGER.debug("Sending frame %s", frame)
                self.send_data(frame.serialize())

                try:
                    async with asyncio_timeout(timeout):
                        return await asyncio.shield(future)
                except asyncio.TimeoutError:
                    _LOGGER.debug(
                        "Failed to send %s, trying again in %0.2fs (attempt %s of %s)",
                        frame,
                        retry_delay,
                        attempt + 1,
                        retries + 1,
                    )

                    if attempt >= retries:
                        raise

                    await asyncio.sleep(retry_delay)
        finally:
            self._pending_frames.pop(frame.payload.command_seq, None)

        raise AssertionError("Unreachable")
