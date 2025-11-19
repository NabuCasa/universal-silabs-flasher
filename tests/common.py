import asyncio
import logging

_LOGGER = logging.getLogger(__name__)


class PairedTransport(asyncio.Transport):
    """A pair of transports that are connected to each other."""

    def __init__(
        self,
        other_protocol: asyncio.Protocol,
        loop: asyncio.AbstractEventLoop,
        chunk_size: int | None = None,
        aggregate_write_timeout: float | None = None,
    ) -> None:
        super().__init__()
        self._other_protocol = other_protocol
        self._loop = loop
        self._closing = False
        self._chunk_size = chunk_size
        self._aggregate_write_timeout = aggregate_write_timeout
        self._aggregate_buffer = bytearray()
        self._aggregate_timer_handle: asyncio.TimerHandle | None = None

    def _flush_aggregate_buffer(self) -> None:
        """Flush the aggregated write buffer."""
        self._aggregate_timer_handle = None

        if not self._aggregate_buffer:
            return

        data = bytes(self._aggregate_buffer)
        self._aggregate_buffer.clear()

        _LOGGER.debug(
            "Flushing aggregated data to %s: %r",
            self._other_protocol.__class__.__name__,
            data,
        )

        if self._chunk_size is None:
            # Send all at once
            self._loop.call_soon(self._other_protocol.data_received, data)
        else:
            # Send in chunks of specified size
            for i in range(0, len(data), self._chunk_size):
                chunk = data[i : i + self._chunk_size]
                self._loop.call_soon(self._other_protocol.data_received, chunk)

    def write(self, data: bytes) -> None:
        _LOGGER.debug(
            "Writing to %s: %r", self._other_protocol.__class__.__name__, data
        )

        if self._aggregate_write_timeout is None:
            # No aggregation, send immediately
            if self._chunk_size is None:
                # Send all at once (default behavior)
                self._loop.call_soon(self._other_protocol.data_received, data)
            else:
                # Send in chunks of specified size
                for i in range(0, len(data), self._chunk_size):
                    chunk = data[i : i + self._chunk_size]
                    self._loop.call_soon(self._other_protocol.data_received, chunk)
        else:
            # Aggregate writes within timeout window
            self._aggregate_buffer.extend(data)

            # Cancel existing timer and schedule a new one
            if self._aggregate_timer_handle is not None:
                self._aggregate_timer_handle.cancel()

            self._aggregate_timer_handle = self._loop.call_later(
                self._aggregate_write_timeout, self._flush_aggregate_buffer
            )

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True

        # Flush any pending aggregated data
        if self._aggregate_timer_handle is not None:
            self._aggregate_timer_handle.cancel()
            self._aggregate_timer_handle = None

        if self._aggregate_buffer:
            self._flush_aggregate_buffer()

        _LOGGER.debug(
            "Closing transport for %s", self._other_protocol.__class__.__name__
        )
        self._loop.call_soon(self._other_protocol.connection_lost, None)
