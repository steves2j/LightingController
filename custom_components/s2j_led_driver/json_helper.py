"""JSON processing helper that coordinates SerialHelper instances."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

from .serial_helper import SerialHelper


_LOGGER = logging.getLogger(__name__)


@dataclass
class JsonHelper:
    """Single coordinator for multiple SerialHelper instances."""

    helpers: Dict[str, SerialHelper] = field(default_factory=dict)
    listeners: Dict[str, List[Callable[[str, dict[str, Any]], None]]] = field(default_factory=dict)
    helper_tasks: Dict[str, asyncio.Task] = field(default_factory=dict)

    def register_helper(self, name: str, helper: SerialHelper) -> None:
        """Register or replace a serial helper by name and start listening."""
        self.helpers[name] = helper
        self._ensure_listener_task(name, helper)

    def unregister_helper(self, name: str) -> None:
        """Remove a helper reference and stop listening."""
        self.helpers.pop(name, None)
        task = self.helper_tasks.pop(name, None)
        if task is not None:
            task.cancel()

    def get_helper(self, name: str) -> SerialHelper | None:
        """Return a helper by name if present."""
        return self.helpers.get(name)

    def register_listener(self, event: str, callback: Callable[[str, dict[str, Any]], None]) -> None:
        """Register a callback for a specific event key."""
        self.listeners.setdefault(event, []).append(callback)

    async def async_send(self, helper_name: str, payload: dict[str, Any]) -> None:
        """Serialize payload to JSON and enqueue it on the selected helper."""
        helper = self.get_helper(helper_name)
        if helper is None:
            raise ValueError(f"Helper '{helper_name}' is not registered")
        message = json.dumps(payload, separators=(",", ":"))
        await helper.async_send(message)

    def _ensure_listener_task(self, name: str, helper: SerialHelper) -> None:
        existing = self.helper_tasks.get(name)
        if existing is not None and not existing.done():
            return

        async def _listener_loop() -> None:
            try:
                while True:
                    queue = helper.read_queue
                    try:
                        loop = asyncio.get_running_loop()
                        start = loop.time()
                        line = await asyncio.wait_for(queue.get(), timeout=1.0)
                        elapsed = loop.time() - start
                        if elapsed > 0.25:
                            _LOGGER.debug(
                                "JsonHelper dequeue delay %.3fs helper=%s queue=%s",
                                elapsed,
                                name,
                                queue.qsize(),
                            )
                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        raise

                    if line is None:
                        continue

                    try:
                        message = json.loads(line)
                    except json.JSONDecodeError:
                        _LOGGER.warning("Failed to decode JSON from %s: %s", name, line)
                        continue

                    message_type = message.get("t") or message.get("type")
                    if message_type == "event":
                        event = message.get("ev")
                        if not isinstance(event, str):
                            continue
                    elif message_type == "status":
                        event = "status"
                    else:
                        continue

                    callbacks = self.listeners.get(event, [])
                    if not callbacks:
                        continue

                    _LOGGER.debug(
                        "JsonHelper dispatch helper=%s event=%s callbacks=%s",
                        name,
                        event,
                        len(callbacks),
                    )
                    for callback in list(callbacks):
                        try:
                            result = callback(name, message)
                            if asyncio.iscoroutine(result):
                                asyncio.create_task(result)
                        except Exception:  # pragma: no cover - safety net
                            _LOGGER.exception("Listener for event %s raised an exception", event)
            except asyncio.CancelledError:
                raise
            finally:
                stored = self.helper_tasks.get(name)
                if stored is asyncio.current_task():
                    self.helper_tasks.pop(name, None)

        self.helper_tasks[name] = asyncio.create_task(_listener_loop())
