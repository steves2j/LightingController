"""Lightweight async serial helper scaffold."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Optional

import serial  # type: ignore[import-untyped]
import serial_asyncio  # type: ignore[import-untyped]

_LOGGER = logging.getLogger(__name__)


class SerialHelperError(Exception):
    """Raised when the serial helper encounters an error."""


class SerialHelper:
    """Basic wrapper for managing an async serial connection."""

    def __init__(self, *, port: str, baudrate: int) -> None:
        self._port = port
        self._baudrate = baudrate
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._read_queue: asyncio.Queue[str] = asyncio.Queue()
        self._write_queue: asyncio.Queue[str] = asyncio.Queue()
        self._reader_task: Optional[asyncio.Task] = None
        self._writer_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._reconnect_delay = 1.0
        self._last_reconnect_error: Optional[str] = None
        self._last_error: Optional[str] = None
        self._closing = False
        self._reconnecting = False

    @property
    def port(self) -> str:
        return self._port

    @property
    def baudrate(self) -> int:
        return self._baudrate

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    async def async_connect(self) -> None:
        """Open the serial connection."""
        if self.is_connected:
            return

        current_task = asyncio.current_task()
        if self._reconnect_task is not None and self._reconnect_task is not current_task:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None

        try:
            self._reader, self._writer = await serial_asyncio.open_serial_connection(
                url=self._port,
                baudrate=self._baudrate,
            )
        except serial.SerialException as err:
            message = f"Unable to open serial port {self._port}: {err}"
            self._last_error = message
            raise SerialHelperError(message) from err

        _LOGGER.debug("SerialHelper connected to %s at %s baud", self._port, self._baudrate)
        self._last_error = None
        self._ensure_reader()
        self._ensure_writer()
        self._reconnecting = False

    async def async_close(self) -> None:
        """Close the serial connection."""
        self._closing = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task
            self._reconnect_task = None

        await self._teardown_connection(clear_queues=True)
        self._closing = False
        self._reconnecting = False

    @property
    def read_queue(self) -> asyncio.Queue[str]:
        """Queue containing raw lines read from the serial connection."""
        return self._read_queue

    @property
    def write_queue(self) -> asyncio.Queue[str]:
        """Queue of commands pending transmission."""
        return self._write_queue

    async def async_send(self, line: str) -> None:
        """Enqueue a command line for transmission."""
        queue_size = self._write_queue.qsize()
        _LOGGER.debug("SerialHelper enqueue TX (size=%s): %s", queue_size, line.rstrip())
        await self._write_queue.put(line)
        _LOGGER.debug("SerialHelper queued TX (size=%s): %s", self._write_queue.qsize(), line.rstrip())

    def _ensure_reader(self) -> None:
        if self._reader is None or self._reader_task is not None:
            return

        async def _reader_loop() -> None:
            try:
                while True:
                    # accumulate bytes until newline is received
                    line = b""
                    while True:
                        chunk = await self._reader.read(1)
                        if not chunk:
                            break
                        line += chunk
                        #_LOGGER.debug("SerialHelper reader received chunk: %s", chunk)
                        if chunk == b"\n":
                            break
                    if not line:
                        _LOGGER.debug("SerialHelper reader reached EOF")
                        await self._handle_serial_failure()
                        break
                    decoded = line.decode("utf-8", errors="replace").rstrip("\r\n")
                    # NOTE: first chunk uses labeled debug, subsequent chunks are raw for diagnostics.
                    if decoded:
                        first_chunk = decoded[:800]
                        _LOGGER.debug("SerialHelper RX line: %s", first_chunk)
                        offset = 800
                        while offset < len(decoded):
                            _LOGGER.debug("%s", decoded[offset : offset + 800])
                            offset += 800
                        
                        await self._read_queue.put(decoded)
                    else:
                        _LOGGER.debug("SerialHelper RX line: <empty>")
                    
            except asyncio.CancelledError:  # pragma: no cover - cooperative shutdown
                raise
            except Exception as err:  # pragma: no cover - diagnostics
                _LOGGER.exception("SerialHelper reader loop failed")
                await self._handle_serial_failure(err)
            finally:
                self._reader_task = None

        self._reader_task = asyncio.create_task(_reader_loop())

    def _ensure_writer(self) -> None:
        if self._writer is None or self._writer_task is not None:
            return

        async def _writer_loop() -> None:
            try:
                while True:
                    line = await self._write_queue.get()
                    _LOGGER.debug("SerialHelper dequeued TX (size=%s): %s", self._write_queue.qsize(), line.rstrip())
                    if self._writer is None:
                        _LOGGER.debug("Writer loop received data after disconnect")
                        break
                    payload = f"{line.rstrip()}\r\n".encode("utf-8")
                    _LOGGER.debug("SerialHelper TX line: %s", line.rstrip())
                    self._writer.write(payload)
                    try:
                        await self._writer.drain()
                        _LOGGER.debug("SerialHelper TX drain ok (size=%s)", self._write_queue.qsize())
                    except serial.SerialException as err:
                        _LOGGER.error("SerialHelper write failed: %s", err)
                        await self._handle_serial_failure(err)
                        break
            except asyncio.CancelledError:  # pragma: no cover - cooperative shutdown
                raise
            except Exception:  # pragma: no cover - diagnostics
                _LOGGER.exception("SerialHelper writer loop failed")
                await self._handle_serial_failure()
            finally:
                self._writer_task = None

        self._writer_task = asyncio.create_task(_writer_loop())

    async def _handle_serial_failure(self, error: Optional[Exception] = None) -> None:
        if error:
            _LOGGER.warning("SerialHelper detected serial failure: %s", error)
            self._last_error = str(error)
        if self._closing:
            return
        if self._reconnecting:
            return
        self._reconnecting = True
        await self._teardown_connection(clear_queues=False)
        self._schedule_reconnect()

    async def _teardown_connection(self, *, clear_queues: bool) -> None:
        current_task = asyncio.current_task()
        reader_task = self._reader_task
        self._reader_task = None
        if reader_task is not None and reader_task is not current_task:
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task

        writer_task = self._writer_task
        self._writer_task = None
        if writer_task is not None and writer_task is not current_task:
            writer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await writer_task

        if self._writer is not None:
            try:
                self._writer.close()
                await self._writer.wait_closed()  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover
                _LOGGER.debug("SerialHelper teardown writer close raised", exc_info=True)

        self._reader = None
        self._writer = None

        if clear_queues:
            self._read_queue = asyncio.Queue()
            self._write_queue = asyncio.Queue()

    def _schedule_reconnect(self) -> None:
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return

        async def _reconnect_loop() -> None:
            try:
                while not self._closing:
                    try:
                        await self.async_connect()
                        _LOGGER.info("SerialHelper reconnected to %s", self._port)
                        self._reconnecting = False
                        self._last_reconnect_error = None
                        return
                    except SerialHelperError as err:
                        message = str(err)
                        if message != self._last_reconnect_error:
                            _LOGGER.warning("SerialHelper reconnect failed: %s", message)
                            self._last_reconnect_error = message
                        await asyncio.sleep(self._reconnect_delay)
            finally:
                self._reconnecting = False
                self._reconnect_task = None

        self._reconnect_task = asyncio.create_task(_reconnect_loop())
