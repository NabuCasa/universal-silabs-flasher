from __future__ import annotations

import asyncio
import dataclasses
import logging
import typing

from zigpy.serial import SerialProtocol
import zigpy.types

from .common import Version, asyncio_timeout, crc16_kermit
from .spinel_types import CommandID, HDLCSpecial, PropertyID, ResetReason

_LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class HDLCLiteFrame:
    data: bytes

    def serialize(self) -> bytes:
        payload = self.data + crc16_kermit(self.data).to_bytes(2, "little")
        encoded = bytearray()

        for byte in payload:
            if byte in (
                HDLCSpecial.FLAG,
                HDLCSpecial.ESCAPE,
                HDLCSpecial.XON,
                HDLCSpecial.XOFF,
                HDLCSpecial.VENDOR,
            ):
                encoded.append(HDLCSpecial.ESCAPE)
                byte ^= 0x20

            encoded.append(byte)

        return bytes([HDLCSpecial.FLAG]) + bytes(encoded) + bytes([HDLCSpecial.FLAG])

    @classmethod
    def from_bytes(cls, data: bytes) -> HDLCLiteFrame:
        unescaped = bytearray()
        unescaping = False

        for byte in data:
            if unescaping:
                byte ^= 0x20

                if byte not in (
                    HDLCSpecial.FLAG,
                    HDLCSpecial.ESCAPE,
                    HDLCSpecial.XON,
                    HDLCSpecial.XOFF,
                    HDLCSpecial.VENDOR,
                ):
                    raise ValueError(f"Invalid unescaped byte: 0x{byte:02X}")

                unescaping = False
            elif byte == HDLCSpecial.ESCAPE:
                unescaping = True
                continue
            elif byte == HDLCSpecial.FLAG:
                continue

            unescaped.append(byte)

        data = unescaped[:-2]
        crc = unescaped[-2:]
        computed_crc = crc16_kermit(data).to_bytes(2, "little")

        if computed_crc != crc:
            raise ValueError(f"Invalid CRC-16: expected {crc!r}, got {computed_crc!r}")

        return cls(data=bytes(data))


class SpinelHeader(zigpy.types.Struct):
    # TODO: allow specifying struct endianness
    transaction_id: zigpy.types.uint4_t
    network_link_id: zigpy.types.uint2_t
    flag: zigpy.types.uint2_t


@dataclasses.dataclass(frozen=True)
class SpinelFrame:
    header: SpinelHeader
    command_id: CommandID
    data: bytes

    @classmethod
    def from_bytes(cls, data: bytes) -> SpinelFrame:
        orig_data = data
        header, data = SpinelHeader.deserialize(data)

        if header.flag != 0b10:
            raise ValueError(f"Spinel header flag is invalid in frame: {orig_data!r}")

        command_id, data = CommandID.deserialize(data)

        return cls(header=header, command_id=command_id, data=data)

    def serialize(self) -> bytes:
        return self.header.serialize() + self.command_id.serialize() + self.data


class SpinelProtocol(SerialProtocol):
    _buffer: bytearray

    def __init__(self) -> None:
        super().__init__()
        self._transaction_id: int = 1
        self._pending_frames: dict[int, asyncio.Future] = {}

    def send_data(self, data: bytes) -> None:
        assert self._transport is not None
        _LOGGER.debug("Sending data %s", data)
        self._transport.write(data)

    def data_received(self, data: bytes) -> None:
        super().data_received(data)

        self._buffer = self._buffer.lstrip(bytes([HDLCSpecial.FLAG]))

        if bytes([HDLCSpecial.FLAG]) not in self._buffer:
            return

        while self._buffer:
            # Flag bytes can come before and after any packet, any number of times
            chunk, _, self._buffer = self._buffer.partition(bytes([HDLCSpecial.FLAG]))

            if not chunk:
                continue

            # Decode the HDLC frame
            try:
                hdlc_frame = HDLCLiteFrame.from_bytes(chunk)
            except ValueError:
                _LOGGER.debug("Failed to decode HDLC chunk %r", chunk)
                continue

            _LOGGER.debug("Decoded HDLC frame: %r", hdlc_frame)

            # And finally the Spinel frame
            try:
                spinel_frame = SpinelFrame.from_bytes(hdlc_frame.data)
            except ValueError as e:
                _LOGGER.debug("Failed to decode Spinel frame: %r", e)
                continue

            self.frame_received(spinel_frame)

    def frame_received(self, frame: SpinelFrame) -> None:
        _LOGGER.debug("Parsed frame %r", frame)

        if frame.header.transaction_id in self._pending_frames:
            self._pending_frames[frame.header.transaction_id].set_result(frame)

    @typing.overload
    async def send_frame(
        self,
        frame: SpinelFrame,
        *,
        wait_response: typing.Literal[True],
        retries: int,
        timeout: float,
        retry_delay: float,
    ) -> None: ...

    @typing.overload
    async def send_frame(
        self,
        frame: SpinelFrame,
        *,
        wait_response: typing.Literal[False],
        retries: int,
        timeout: float,
        retry_delay: float,
    ) -> SpinelFrame: ...

    async def send_frame(
        self,
        frame: SpinelFrame,
        *,
        wait_response: bool = True,
        retries: int = 3,
        timeout: float = 1,
        retry_delay: float = 0.1,
    ) -> SpinelFrame | None:
        # A transaction ID of `0` is special: we only use 1-15
        self._transaction_id = (self._transaction_id + 1) % (0b1111 - 1)
        tid = 1 + self._transaction_id

        future = asyncio.get_running_loop().create_future()
        self._pending_frames[tid] = future

        # Replace the transaction ID
        new_frame = dataclasses.replace(
            frame, header=frame.header.replace(transaction_id=tid)
        )

        if not wait_response:
            _LOGGER.debug("Sending frame %r", new_frame)
            self.send_data(HDLCLiteFrame(data=new_frame.serialize()).serialize())
            return None

        try:
            for attempt in range(retries + 1):
                _LOGGER.debug("Sending frame %r", new_frame)
                self.send_data(HDLCLiteFrame(data=new_frame.serialize()).serialize())

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
            del self._pending_frames[tid]

        raise AssertionError("Unreachable")

    async def send_command(
        self, command_id: CommandID, data: bytes, **kwargs
    ) -> SpinelFrame:
        frame = SpinelFrame(
            header=SpinelHeader(
                flag=0b10,
                network_link_id=0,
                transaction_id=None,
            ),
            command_id=command_id,
            data=data,
        )

        return await self.send_frame(frame, **kwargs)

    async def probe(self) -> Version:
        rsp = await self.send_command(
            CommandID.PROP_VALUE_GET,
            PropertyID.NCP_VERSION.serialize(),
        )

        prop_id, version_string = PropertyID.deserialize(rsp.data)
        assert prop_id == PropertyID.NCP_VERSION

        # SL-OPENTHREAD/2.2.2.0_GitHub-91fa1f455; EFR32; Mar 14 2023 16:03:40
        version = version_string.rstrip(b"\x00").decode("ascii")

        # We strip off the date code to get something reasonably stable
        short_version, _ = version.split(";", 1)

        return Version(short_version)

    async def enter_bootloader(self) -> None:
        await self.send_command(
            CommandID.RESET,
            ResetReason.BOOTLOADER.serialize(),
            wait_response=False,
        )
