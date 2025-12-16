"""Async JSON-over-serial helper for the LED driver."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import serial  # type: ignore[import-untyped]
import serial_asyncio  # type: ignore[import-untyped]

_LOGGER = logging.getLogger(__name__)

_COMMAND_RESPONSE_TIMEOUT = 20.0


class LedDriverError(Exception):
    """Raised when the LED driver encounters an error."""


class LedDriverClient:
    """Manage the transport to the LED driver USB serial interface."""

    def __init__(
        self,
        *,
        port: str,
        baudrate: int,
        event_callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._event_callback = event_callback
        self._reader_task: asyncio.Task | None = None
        self._pending_command: _PendingCommand | None = None

    @property
    def port(self) -> str:
        return self._port

    @property
    def baudrate(self) -> int:
        return self._baudrate

    @property
    def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def async_connect(self) -> None:
        """Open the serial connection."""
        if self.is_connected:
            return

        try:
            self._reader, self._writer = await serial_asyncio.open_serial_connection(
                url=self._port,
                baudrate=self._baudrate,
            )
        except serial.SerialException as err:
            raise LedDriverError(f"Unable to open serial port {self._port}: {err}") from err

        _LOGGER.debug("Serial port %s opened at %s baud", self._port, self._baudrate)
        self._ensure_reader_task()

    async def async_close(self) -> None:
        """Close the serial connection."""
        if self._writer is None and self._reader_task is None:
            return

        if self._writer is not None:
            _LOGGER.debug("Closing serial port %s", self._port)
            self._writer.close()
            try:
                await self._writer.wait_closed()  # type: ignore[func-returns-value]
            except Exception:  # pragma: no cover - safe guard
                _LOGGER.debug("Serial writer close raised", exc_info=True)
        self._reader = None
        self._writer = None

        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        self._fail_pending(LedDriverError("Serial connection closed"))

    async def async_get_state(self, groups: Sequence[str]) -> dict[str, Any]:
        """Request the current state for the provided groups."""
        payload: dict[str, Any] = {
            "type": "get_state",
            "groups": list(groups) if groups else None,
        }
        response = await self._async_round_trip(payload)
        return response.get("groups", {})

    async def async_send_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send a raw JSON command and return the driver response."""
        return await self._async_round_trip(payload)

    async def async_set_group(
        self,
        *,
        group_id: str,
        brightness: int | None = None,
        is_on: bool | None = None,
    ) -> dict[str, Any]:
        """Send a command to modify a group."""
        payload: dict[str, Any] = {
            "type": "set_group",
            "group_id": group_id,
        }
        if brightness is not None:
            payload["brightness"] = brightness
        if is_on is not None:
            payload["on"] = is_on

        return await self._async_round_trip(payload)

    def set_event_callback(
        self, callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None
    ) -> None:
        """Register a coroutine to handle event messages."""

        self._event_callback = callback

    def _ensure_reader_task(self) -> None:
        if self._reader is None or self._writer is None:
            return
        if self._reader_task is None or self._reader_task.done():
            self._reader_task = asyncio.create_task(self._reader_loop())

    def _fail_pending(self, error: Exception) -> None:
        pending = self._pending_command
        if pending and not pending.future.done():
            pending.future.set_exception(error)
        self._pending_command = None

    def _resolve_pending(self, result: dict[str, Any]) -> None:
        pending = self._pending_command
        if pending and not pending.future.done():
            pending.future.set_result(result)
            self._pending_command = None

    def _handle_event_for_pending(self, event: dict[str, Any]) -> None:
        pending = self._pending_command
        if pending is None or pending.future.done():
            _LOGGER.debug("Event %s received but no pending command", event)
            return

        if not pending.expected_leds:
            _LOGGER.debug("Event %s ignored because command expects explicit success", event)
            return

        key = self._extract_led_key(event)
        if key is None:
            _LOGGER.debug("Event %s missing driver/channel identifiers", event)
            return

        if key in pending.expected_leds:
            pending.expected_leds.remove(key)
            if not pending.expected_leds:
                _LOGGER.debug("Completing command from event %s", event)
                self._resolve_pending(event)
        else:
            _LOGGER.debug(
                "Event %s for key %s not in pending set %s",
                event,
                key,
                pending.expected_leds,
            )

    def _handle_response_for_pending(self, message: dict[str, Any]) -> None:
        pending = self._pending_command
        if pending is None or pending.future.done():
            return

        message_type = (message.get("t") or message.get("type") or "").lower()
        _LOGGER.debug("Serial command response candidate: %s", message)
        if message_type == "error":
            reason = message.get("reason") or message.get("rs") or "Command failed"
            self._fail_pending(LedDriverError(str(reason)))
            return

        if message_type in {"success", "ok", ""}:
            self._resolve_pending(message)

    def _loop_create_future(self) -> asyncio.Future[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        return loop.create_future()

    async def _reader_loop(self) -> None:
        try:
            while True:
                if self._reader is None:
                    break
                message = await self._async_read_with_timeout(0.5)
                if message is None:
                    if not self.is_connected:
                        break
                    continue
                if message.get("t") == "event":
                    await self._async_dispatch_event(message)
                    self._handle_event_for_pending(message)
                else:
                    self._handle_response_for_pending(message)
        except asyncio.CancelledError:  # pragma: no cover - cooperative cancellation
            raise
        except LedDriverError as err:
            _LOGGER.debug("Serial reader loop stopped: %s", err)
            self._fail_pending(err)
        except Exception as err:  # pragma: no cover - unexpected reader failures
            _LOGGER.exception("Serial reader loop crashed", exc_info=True)
            self._fail_pending(LedDriverError(f"Serial reader failure: {err}"))
        finally:
            self._reader_task = None

    def _build_led_expectations(self, payload: dict[str, Any]) -> set[tuple[int, int]] | None:
        expected: set[tuple[int, int]] = set()
        for entry in payload.get("drvs", []):
            driver_idx = self._safe_int(entry.get("drv", entry.get("dvr")))
            if driver_idx is None:
                continue
            channels = entry.get("cs") or entry.get("c") or entry.get("channels") or []
            for channel in channels:
                channel_idx = self._safe_int(channel)
                if channel_idx is None:
                    continue
                expected.add((driver_idx, channel_idx))
        return expected or None

    @staticmethod
    def _extract_led_key(event: dict[str, Any]) -> tuple[int, int] | None:
        driver_idx = None
        for key in ("dvr", "drv", "driver"):
            if key in event:
                driver_idx = _safe_int_value(event.get(key))
                break
        channel_idx = None
        for key in ("c", "channel", "idx", "index"):
            if key in event:
                channel_idx = _safe_int_value(event.get(key))
                break
        if driver_idx is None or channel_idx is None:
            _LOGGER.debug("Unable to derive LED key from event %s", event)
            return None
        return (driver_idx, channel_idx)

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        return _safe_int_value(value)

    async def _async_round_trip(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send JSON and wait for a JSON response (events are dispatched)."""

        async with self._lock:
            self._ensure_reader_task()
            if self._pending_command and not self._pending_command.future.done():
                raise LedDriverError("Command already in progress")

            expected_leds: set[tuple[int, int]] | None = None
            if payload.get("cm") == "led":
                expected_leds = self._build_led_expectations(payload)
                _LOGGER.debug("Prepared LED expectations: %s", expected_leds)

            future: asyncio.Future[dict[str, Any]] = self._loop_create_future()
            self._pending_command = _PendingCommand(
                future=future,
                expected_leds=expected_leds,
            )

            await self._async_send(payload)
            try:
                response = await asyncio.wait_for(future, timeout=_COMMAND_RESPONSE_TIMEOUT)
            except asyncio.TimeoutError as err:
                self._fail_pending(LedDriverError("Timed out waiting for LED driver response"))
                raise LedDriverError("Timed out waiting for LED driver response") from err
            finally:
                if self._pending_command and self._pending_command.future.done():
                    self._pending_command = None

            return response

    async def _async_send(self, payload: dict[str, Any]) -> None:
        """Write a JSON payload to the serial port."""
        if self._writer is None:
            raise LedDriverError("Serial writer is not ready")

        message = json.dumps(payload, separators=(",", ":")) + "\n"
        _LOGGER.debug("TX: %s", message.strip())
        self._writer.write(message.encode("utf-8"))

        try:
            await self._writer.drain()
        except serial.SerialException as err:
            raise LedDriverError(f"Serial write failed: {err}") from err

    async def _async_read(self) -> dict[str, Any]:
        """Read a JSON message from the serial port."""
        if self._reader is None:
            raise LedDriverError("Serial reader is not ready")

        try:
            raw = await asyncio.wait_for(self._reader.readline(), timeout=5)
        except asyncio.TimeoutError as err:
            raise LedDriverError("Timed out waiting for LED driver response") from err

        if not raw:
            raise LedDriverError("LED driver closed the connection")

        _LOGGER.debug("Raw RX bytes: %r", raw)
        return self._decode_message(raw)

    async def _async_read_with_timeout(self, timeout: float) -> dict[str, Any] | None:
        """Read a JSON message but return None on timeout."""

        if self._reader is None:
            raise LedDriverError("Serial reader is not ready")

        try:
            raw = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

        if not raw:
            raise LedDriverError("LED driver closed the connection")

        _LOGGER.debug("Raw RX bytes (timeout read): %r", raw)
        return self._decode_message(raw)

    async def _async_dispatch_event(self, message: dict[str, Any]) -> None:
        """Send an event message to the registered callback."""

        if self._event_callback is None:
            return

        try:
            result = self._event_callback(message)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # pragma: no cover - safety net
            _LOGGER.exception("LED driver event handler raised")
 
    def _decode_message(self, raw: bytes) -> dict[str, Any]:
        message = raw.decode("utf-8", errors="replace").strip()
        if not message:
            raise LedDriverError("Received empty message from LED driver")
        _LOGGER.debug("RX: %s", message)

        try:
            return json.loads(message)
        except json.JSONDecodeError as err:
            raise LedDriverError(f"Invalid JSON from LED driver: {message}") from err


def _safe_int_value(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class _PendingCommand:
    future: asyncio.Future[dict[str, Any]]
    expected_leds: set[tuple[int, int]] | None
