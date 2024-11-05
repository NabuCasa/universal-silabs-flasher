from __future__ import annotations

import asyncio
import dataclasses
import logging
import typing

import zigpy.types

from .common import asyncio_timeout, crc16_ccitt

_LOGGER = logging.getLogger(__name__)

BLOCK_SIZE = 128
RECEIVE_TIMEOUT = 2

_WRITER_GRAVEYARD: list[asyncio.StreamWriter] = []


class PacketType(zigpy.types.enum8):
    """XModem packet type byte."""

    SOH = 0x01  # Start of Header
    EOT = 0x04  # End of Transmission
    CAN = 0x18  # Cancel
    ETB = 0x17  # End of Transmission Block
    ACK = 0x06  # Acknowledge
    NAK = 0x15  # Not Acknowledge


@dataclasses.dataclass(frozen=True)
class XmodemCRCPacket:
    """XModem CRC packet implementing the zigpy `serialize` API."""

    number: zigpy.types.uint8_t
    payload: bytes

    def serialize(self) -> bytes:
        """Serialize the packet, computing header and payload checksums."""
        assert len(self.payload) == BLOCK_SIZE
        return (
            bytes([PacketType.SOH, self.number, 0xFF - self.number])
            + self.payload
            + crc16_ccitt(self.payload).to_bytes(2, "big")
        )


class ReceiverCancelled(Exception):
    """Receiver cancelled the transmission with a `CAN` status."""


async def send_xmodem128_crc_data(
    data: bytes,
    *,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamReader,
    max_failures: int,
) -> None:
    """Send `data` to an XModem receiver until an ACK is received."""

    for attempt in range(max_failures + 1):
        # Send off the data
        _LOGGER.debug("Sending data %r (attempt %d)", data, attempt)
        writer.write(data)
        await writer.drain()

        # And wait for a response
        async with asyncio_timeout(RECEIVE_TIMEOUT):
            rsp_byte = await reader.readexactly(1)

        _LOGGER.debug("Got response: %r", rsp_byte)

        if rsp_byte[0] == PacketType.ACK:
            return
        elif rsp_byte[0] == PacketType.NAK:
            _LOGGER.debug("Got a NAK, retrying")

            if attempt >= max_failures:
                raise ValueError(f"Received {max_failures} consecutive failures")
        elif rsp_byte[0] == PacketType.CAN:
            raise ReceiverCancelled()
        else:
            raise ValueError(f"Invalid response: {rsp_byte!r}")


async def send_xmodem128_crc(
    data: bytes,
    *,
    transport: asyncio.Transport,
    max_failures: int = 3,
    progress_callback: typing.Callable[[int, int], typing.Any] | None = None,
) -> None:
    """Send `data` over `transport` using XModemCRC with a 128 byte block size."""

    if len(data) % BLOCK_SIZE != 0:
        raise ValueError(f"Data length must be divisible by {BLOCK_SIZE}: {len(data)}")

    loop = asyncio.get_running_loop()

    # Initialization of the reader and writer objects is from `asyncio.open_connection`
    reader = asyncio.StreamReader(limit=65536, loop=loop)
    protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)

    # Swap protocols
    old_protocol = transport.get_protocol()
    transport.set_protocol(protocol)

    try:
        # Read until the first ASCII "C"
        await reader.readuntil(b"C")

        if progress_callback is not None:
            progress_callback(0, len(data))

        # FIXME: ensure any subsequent "C"s have been cleared so they do not interfere
        reader._buffer.clear()

        for index in range(0, len(data) // BLOCK_SIZE):
            packet = XmodemCRCPacket(
                number=(index + 1) & 0xFF,  # `seq` starts at 1 and then wraps
                payload=data[BLOCK_SIZE * index : BLOCK_SIZE * (index + 1)],
            )

            # Send the packet
            await send_xmodem128_crc_data(
                data=packet.serialize(),
                reader=reader,
                writer=writer,
                max_failures=max_failures,
            )

            offset = (index + 1) * BLOCK_SIZE

            if progress_callback is not None:
                progress_callback(offset, len(data))

            _LOGGER.debug("Firmware upload progress: %0.2f%%", 100 * offset / len(data))

        # Once we are done, finalize the upload
        await send_xmodem128_crc_data(
            data=bytes([PacketType.EOT]),
            reader=reader,
            writer=writer,
            max_failures=max_failures,
        )
    finally:
        # XXX: Make sure the writer doesn't close our transport when garbage collected
        global _WRITER_GRAVEYARD

        _WRITER_GRAVEYARD.append(writer)
        _WRITER_GRAVEYARD = [
            w
            for w in _WRITER_GRAVEYARD
            if w.transport is not None and not w.transport.is_closing()
        ]

        # Reset the old protocol
        transport.set_protocol(old_protocol)

        # Send our reader's buffer to the old protocol
        data = bytes(reader._buffer)

        if data:
            _LOGGER.debug("Sending remaining reader data to old protocol: %r", data)
            old_protocol.data_received(data)
