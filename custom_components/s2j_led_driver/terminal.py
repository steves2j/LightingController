"""Websocket terminal support for LED driver debug serial ports."""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .manager import LedDriverError, LedDriverManager

_LOGGER = logging.getLogger(__name__)
_SESSION_SUBSCRIPTIONS: dict[str, int] = {}


async def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register websocket commands for the debug terminal."""
    websocket_api.async_register_command(hass, websocket_terminal_connect)
    websocket_api.async_register_command(hass, websocket_terminal_connect_port)
    websocket_api.async_register_command(hass, websocket_terminal_input)
    websocket_api.async_register_command(hass, websocket_terminal_disconnect)
    websocket_api.async_register_command(hass, websocket_terminal_resize)


def _get_manager(hass: HomeAssistant, entry_id: str) -> LedDriverManager:
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if entry_data is None:
        raise LedDriverError("Invalid entry id")
    return entry_data["manager"]


@websocket_api.websocket_command(
    {
        vol.Required("type"): "s2j_led_driver/terminal/connect",
        vol.Required("entry_id"): str,
        vol.Required("controller_id"): str,
    }
)
@websocket_api.async_response
async def websocket_terminal_connect(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Open a terminal session and stream raw serial data as websocket events."""
    manager = _get_manager(hass, msg["entry_id"])
    try:
        session_id, helper = await manager.async_open_terminal(msg["controller_id"])
    except LedDriverError as err:
        connection.send_error(msg["id"], websocket_api.ERR_HOME_ASSISTANT_ERROR, str(err))
        return

    cancelled = False

    async def _reader() -> None:
        try:
            connection.send_event(
                msg["id"],
                {"event": "status", "connected": True, "session_id": session_id},
            )
            while True:
                chunk = await helper.read_queue.get()
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                connection.send_event(
                    msg["id"],
                    {
                        "event": "data",
                        "data": base64.b64encode(chunk).decode("ascii"),
                    },
                )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # pragma: no cover - defensive stream guard
            _LOGGER.debug("Terminal stream stopped: %s", err)
            connection.send_event(
                msg["id"],
                {"event": "status", "connected": False, "error": str(err)},
            )

    task: asyncio.Task | None = None

    @callback
    def _cleanup() -> None:
        nonlocal cancelled
        if cancelled:
            return
        cancelled = True
        _SESSION_SUBSCRIPTIONS.pop(session_id, None)
        if task is not None:
            task.cancel()
        hass.async_create_task(manager.async_close_terminal(session_id))

    connection.subscriptions[msg["id"]] = _cleanup
    _SESSION_SUBSCRIPTIONS[session_id] = msg["id"]
    connection.send_result(msg["id"], {"session_id": session_id})
    task = hass.async_create_background_task(
        _reader(),
        "s2j_led_driver_terminal_reader",
        eager_start=True,
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "s2j_led_driver/terminal/connect_port",
        vol.Required("entry_id"): str,
        vol.Required("port"): str,
        vol.Optional("baudrate", default=115200): int,
    }
)
@websocket_api.async_response
async def websocket_terminal_connect_port(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Open a terminal session for an explicit raw serial device path."""
    manager = _get_manager(hass, msg["entry_id"])
    try:
        session_id, helper = await manager.async_open_terminal_port(msg["port"], msg["baudrate"])
    except LedDriverError as err:
        connection.send_error(msg["id"], websocket_api.ERR_HOME_ASSISTANT_ERROR, str(err))
        return

    cancelled = False

    async def _reader() -> None:
        try:
            connection.send_event(
                msg["id"],
                {"event": "status", "connected": True, "session_id": session_id},
            )
            while True:
                chunk = await helper.read_queue.get()
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8", errors="replace")
                connection.send_event(
                    msg["id"],
                    {
                        "event": "data",
                        "data": base64.b64encode(chunk).decode("ascii"),
                    },
                )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # pragma: no cover - defensive stream guard
            _LOGGER.debug("Terminal stream stopped: %s", err)
            connection.send_event(
                msg["id"],
                {"event": "status", "connected": False, "error": str(err)},
            )

    task: asyncio.Task | None = None

    @callback
    def _cleanup() -> None:
        nonlocal cancelled
        if cancelled:
            return
        cancelled = True
        _SESSION_SUBSCRIPTIONS.pop(session_id, None)
        if task is not None:
            task.cancel()
        hass.async_create_task(manager.async_close_terminal(session_id))

    connection.subscriptions[msg["id"]] = _cleanup
    _SESSION_SUBSCRIPTIONS[session_id] = msg["id"]
    connection.send_result(msg["id"], {"session_id": session_id})
    task = hass.async_create_background_task(
        _reader(),
        "s2j_led_driver_terminal_reader",
        eager_start=True,
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "s2j_led_driver/terminal/input",
        vol.Required("entry_id"): str,
        vol.Required("session_id"): str,
        vol.Required("data"): str,
    }
)
@websocket_api.async_response
async def websocket_terminal_input(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Write base64-encoded terminal input to a debug serial session."""
    manager = _get_manager(hass, msg["entry_id"])
    try:
        data = base64.b64decode(msg["data"], validate=True)
        await manager.async_terminal_input(msg["session_id"], data)
    except (binascii.Error, LedDriverError) as err:
        connection.send_error(msg["id"], websocket_api.ERR_HOME_ASSISTANT_ERROR, str(err))
        return
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "s2j_led_driver/terminal/disconnect",
        vol.Required("entry_id"): str,
        vol.Required("session_id"): str,
    }
)
@websocket_api.async_response
async def websocket_terminal_disconnect(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Close a terminal session."""
    manager = _get_manager(hass, msg["entry_id"])
    session_id = msg["session_id"]
    if (sub_id := _SESSION_SUBSCRIPTIONS.pop(session_id, None)) is not None:
        if unsub := connection.subscriptions.pop(sub_id, None):
            with contextlib.suppress(Exception):
                unsub()
    else:
        await manager.async_close_terminal(session_id)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "s2j_led_driver/terminal/resize",
        vol.Required("entry_id"): str,
        vol.Required("session_id"): str,
        vol.Required("cols"): int,
        vol.Required("rows"): int,
    }
)
@websocket_api.async_response
async def websocket_terminal_resize(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Accept terminal resize notifications.

    USB serial debug channels do not expose a PTY window-size ioctl, but keeping
    this command lets the browser terminal behave like a normal VT client.
    """
    connection.send_result(msg["id"])
