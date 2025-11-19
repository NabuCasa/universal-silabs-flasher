from __future__ import annotations

import asyncio
from collections import defaultdict
import dataclasses
import logging
import typing
from typing import Callable

from zigpy.serial import SerialProtocol
import zigpy.types

from .common import Version, asyncio_timeout, crc16_kermit
from .spinel_types import (
    CommandID,
    HDLCSpecial,
    PackedUInt21,
    PropertyID,
    ResetReason,
    Status,
)

_LOGGER = logging.getLogger(__name__)

COMMAND_TIMEOUT = 2
RESET_TIMEOUT = 2


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
        self._property_listeners: defaultdict[PropertyID, list[Callable]] = defaultdict(
            list
        )

    def send_data(self, data: bytes) -> None:
        assert self._transport is not None
        _LOGGER.debug("Sending data %s", data)
        self._transport.write(data)

    def data_received(self, data: bytes) -> None:
        super().data_received(data)

        while self._buffer:
            chunk, flag, self._buffer = self._buffer.partition(
                bytes([HDLCSpecial.FLAG])
            )

            # If the flag isn't found, we're done
            if not flag:
                self._buffer = chunk
                break

            # Sometimes the flag can be repeated multiple times
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
            fut = self._pending_frames[frame.header.transaction_id]

            if fut.done():
                _LOGGER.debug(
                    "Ignoring duplicate response for TID %d",
                    frame.header.transaction_id,
                )
            else:
                fut.set_result(frame)

        if frame.command_id == CommandID.PROP_VALUE_IS:
            prop_id, data = PackedUInt21.deserialize(frame.data)
            prop_id = PropertyID(prop_id)

            for listener in self._property_listeners[prop_id]:
                try:
                    listener(data)
                except Exception:
                    _LOGGER.warning(
                        "Error calling property listener for %r: %r",
                        prop_id,
                        listener,
                        exc_info=True,
                    )

    @typing.overload
    async def send_frame(
        self,
        frame: SpinelFrame,
        *,
        wait_response: typing.Literal[False],
        retries: int = ...,
        timeout: float = ...,
        retry_delay: float = ...,
        tid: int | None = ...,
    ) -> None: ...

    @typing.overload
    async def send_frame(
        self,
        frame: SpinelFrame,
        *,
        wait_response: typing.Literal[True],
        retries: int = ...,
        timeout: float = ...,
        retry_delay: float = ...,
        tid: int | None = ...,
    ) -> SpinelFrame: ...

    async def send_frame(
        self,
        frame: SpinelFrame,
        *,
        wait_response: bool = True,
        retries: int = 2,
        timeout: float = COMMAND_TIMEOUT,
        retry_delay: float = 0.1,
        tid: int | None = None,
    ) -> SpinelFrame | None:
        # A transaction ID of `0` is special: we only use 1-15
        if tid is None:
            self._transaction_id = (self._transaction_id + 1) % (0b1111 - 1)
            tid = 1 + self._transaction_id

        # Replace the transaction ID
        new_frame = dataclasses.replace(
            frame, header=frame.header.replace(transaction_id=tid)
        )

        if not wait_response:
            _LOGGER.debug("Sending frame %r", new_frame)
            self.send_data(HDLCLiteFrame(data=new_frame.serialize()).serialize())
            return None

        if tid == 0:
            raise ValueError("Cannot wait for response on TID=0 frames")

        future = asyncio.get_running_loop().create_future()
        self._pending_frames[tid] = future

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

    def add_property_listener(
        self, property_id: PropertyID, callback: Callable[[bytes], None]
    ) -> None:
        self._property_listeners[property_id].append(callback)

    def remove_property_listener(
        self, property_id: PropertyID, callback: Callable[[bytes], None]
    ) -> None:
        self._property_listeners[property_id].remove(callback)

    async def iter_property_changes(
        self, property_id: PropertyID
    ) -> typing.AsyncIterator:
        queue: asyncio.Queue[bytes] = asyncio.Queue()

        try:
            self.add_property_listener(property_id, queue.put_nowait)

            while True:
                item = await queue.get()
                yield item
        finally:
            self.remove_property_listener(property_id, queue.put_nowait)

    async def wait_for_property(self, property_id: PropertyID, value: bytes) -> None:
        async for changed_value in self.iter_property_changes(property_id):
            if changed_value == value:
                return

    async def probe(self) -> Version:
        await self.reset(ResetReason.STACK)

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
        await self.reset(ResetReason.BOOTLOADER)

    async def reset(self, reset_type: ResetReason) -> None:
        await self.send_command(
            CommandID.RESET,
            reset_type.serialize(),
            wait_response=False,
            tid=0,  # Reset uses TID=0
        )

        if reset_type == ResetReason.BOOTLOADER:
            return

        try:
            async with asyncio_timeout(RESET_TIMEOUT):
                await self.wait_for_property(
                    PropertyID.LAST_STATUS, Status.RESET_POWER_ON.serialize()
                )
        except asyncio.TimeoutError:
            # OTBR itself uses this logic, we match it
            _LOGGER.debug("Device did not respond to reset, continuing")
