"""Runtime manager for serial controllers and group actions."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from homeassistant.core import HomeAssistant, callback

from .const import DEFAULT_BAUDRATE
from .registry import LedRegistry, SSR_ALLOWED_BITS
from .serial_helper import SerialHelper, SerialHelperError
from .json_helper import JsonHelper

ControllerListener = Callable[[dict[str, Any]], None]

_LOGGER = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 5
_SWITCH_DOUBLE_PRESS_WINDOW = 0.5
_SWITCH_HOLD_THRESHOLD = 0.4
_PWM_STEP_INTERVAL = 0.3
_PWM_STEP_SIZE = 1
_SSR_MASK = 0xFFFF


@dataclass
class _ButtonState:
    switch: int
    mask: int
    group_id: str
    name: str | None = None
    button_count: int = 5
    direction: int = 1
    last_press: float = 0.0
    last_release: float = 0.0
    pressed: bool = False
    ramp_task: asyncio.Task | None = None
    hold_started: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    pending_toggle: bool = False


def _get(data: dict[str, Any] | None, *keys: str) -> Any:
    if not isinstance(data, dict):
        return None
    for key in keys:
        if key in data:
            return data[key]
    return None


def _get_bool(data: dict[str, Any] | None, *keys: str) -> bool | None:
    value = _get(data, *keys)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "yes", "on"}
    if value is None:
        return None
    return bool(value)


def _extract_button_count(switch: dict[str, Any] | None) -> int:
    """Return a sanitized button count for a switch definition."""

    candidate: Any = None
    if isinstance(switch, dict):
        candidate = switch.get("button_count")
        if candidate is None:
            metadata = switch.get("metadata")
            if isinstance(metadata, dict):
                candidate = metadata.get("button_count")

    try:
        value = int(candidate) if candidate is not None else 5
    except (TypeError, ValueError):
        value = 5

    if value < 1:
        return 1
    if value > 5:
        return 5
    return value


class LedDriverError(Exception):
    """Raised for LED driver management failures."""


class LedDriverManager:
    """Coordinate serial clients and apply group actions."""

    def __init__(self, hass: HomeAssistant, registry: LedRegistry) -> None:
        self._hass = hass
        self._registry = registry
        self._serial_helpers: dict[str, SerialHelper] = {}
        self._controller_listeners: list[ControllerListener] = []
        self._registry_listener: ControllerListener | None = None
        self._json_helper = JsonHelper()
        self._listeners_registered = False
        self._poll_tasks: dict[str, asyncio.Task] = {}
        self._poll_enabled: set[str] = set()
        self._button_states: dict[tuple[int, int], _ButtonState] = {}
        self._switch_buttons: dict[int, list[_ButtonState]] = defaultdict(list)
        self._switch_masks: dict[int, int] = {}

    async def async_initialize(self) -> None:
        """Ensure serial clients exist for stored controllers."""
        await self._sync_clients()
        if self._registry_listener is None:
            self._registry_listener = self._handle_registry_change
            self._registry.async_add_listener(self._registry_listener)

    @callback
    def async_add_controller_listener(self, listener: ControllerListener) -> None:
        self._controller_listeners.append(listener)

    @callback
    def _handle_registry_change(self) -> None:
        self._hass.async_create_task(self._sync_clients())

    async def _sync_clients(self) -> None:
        controllers = {ctrl["id"]: ctrl for ctrl in self._registry.get_controllers()}
        self._register_json_listeners()

        # Remove stale helpers
        for controller_id in list(self._serial_helpers):
            if controller_id not in controllers:
                helper = self._serial_helpers.pop(controller_id)
                await helper.async_close()
                self._json_helper.unregister_helper(controller_id)
                self._stop_polling(controller_id)

        # Create or refresh helpers
        for controller_id, controller in controllers.items():
            port = controller.get("port")
            baudrate = controller.get("baudrate", DEFAULT_BAUDRATE)
            helper = self._serial_helpers.get(controller_id)

            if not port:
                if helper is not None:
                    await helper.async_close()
                    self._json_helper.unregister_helper(controller_id)
                    self._serial_helpers.pop(controller_id, None)
                    self._stop_polling(controller_id)
                continue

            if helper and helper.port == port and helper.baudrate == baudrate:
                continue

            if helper is not None:
                await helper.async_close()
                self._json_helper.unregister_helper(controller_id)

            helper = SerialHelper(port=port, baudrate=baudrate)
            try:
                await helper.async_connect()
            except SerialHelperError as err:
                _LOGGER.warning(
                    "Controller %s failed to connect on %s: %s",
                    controller_id,
                    port,
                    err,
                )
            self._serial_helpers[controller_id] = helper
            self._json_helper.register_helper(controller_id, helper)

        for controller_id, controller in controllers.items():
            self._apply_polling_state(controller_id, controller)

        self._sync_buttons()

        for listener in self._controller_listeners:
            listener(controllers)

    def _register_json_listeners(self) -> None:
        if self._listeners_registered:
            return
        self._json_helper.register_listener("led.enable", self._on_led_channel_state)
        self._json_helper.register_listener("led.channel_state", self._on_led_channel_state)
        self._json_helper.register_listener("led.disable", self._on_led_channel_state)
        self._json_helper.register_listener("led.fault", self._on_led_fault)
        self._json_helper.register_listener("led.fault_cleared", self._on_led_fault_cleared)
        self._json_helper.register_listener("can.message", self._on_can_message)
        self._json_helper.register_listener("status", self._on_status_message)
        self._listeners_registered = True

    def get_controller_serial_status(self) -> dict[str, dict[str, Any]]:
        statuses: dict[str, dict[str, Any]] = {}
        for controller in self._registry.get_controllers():
            controller_id = controller.get("id")
            if not controller_id:
                continue
            helper = self._serial_helpers.get(controller_id)
            is_open = bool(helper and helper.is_connected)
            error = helper.last_error if helper is not None else None
            statuses[controller_id] = {
                "is_open": is_open,
                "error": error,
            }
        return statuses

    def _on_led_channel_state(self, controller_id: str, message: dict[str, Any]) -> None:
        self._hass.async_create_task(self._process_led_channel_state(controller_id, message))

    def _on_led_fault(self, controller_id: str, message: dict[str, Any]) -> None:
        self._hass.async_create_task(self._process_led_fault(controller_id, message, True))

    def _on_led_fault_cleared(self, controller_id: str, message: dict[str, Any]) -> None:
        self._hass.async_create_task(self._process_led_fault(controller_id, message, False))

    def _on_can_message(self, controller_id: str, message: dict[str, Any]) -> None:
        self._hass.async_create_task(self._process_button_event(controller_id, message))

    def _on_status_message(self, controller_id: str, message: dict[str, Any]) -> None:
        async def _handle() -> None:
            await self._registry.async_append_serial_log(controller_id, direction="rx", payload=message)
            await self._handle_status_response(controller_id, message)

        self._hass.async_create_task(_handle())

    async def _process_led_channel_state(self, controller_id: str, message: dict[str, Any]) -> None:
        await self._registry.async_append_serial_log(controller_id, direction="rx", payload=message)
        changed_output_ids = self._apply_channel_state_event(controller_id, message)
        if not changed_output_ids:
            return
        changed_groups = self._update_groups_from_output_ids(changed_output_ids)
        try:
            await self._registry.async_commit()
            if changed_groups:
                await self._broadcast_group_updates(changed_groups)
        except Exception:  # pragma: no cover
            _LOGGER.exception("Failed to commit registry updates from controller event")

    async def _process_led_fault(
        self,
        controller_id: str,
        message: dict[str, Any],
        is_fault: bool,
    ) -> None:
        await self._registry.async_append_serial_log(controller_id, direction="rx", payload=message)
        changed_output_ids = self._apply_fault_event(controller_id, message, is_fault)
        if not changed_output_ids:
            return
        changed_groups = self._update_groups_from_output_ids(changed_output_ids)
        try:
            await self._registry.async_commit()
            if changed_groups:
                await self._broadcast_group_updates(changed_groups)
        except Exception:  # pragma: no cover
            _LOGGER.exception("Failed to commit registry updates from fault event")

    async def _process_button_event(self, controller_id: str, message: dict[str, Any]) -> None:
        await self._registry.async_append_serial_log(controller_id, direction="rx", payload=message)
        await self._handle_button_event(controller_id, message)

    def _get_can_controller_id(self) -> str:
        for controller in self._registry.get_controllers():
            if not controller.get("has_can_interface"):
                continue
            controller_id = controller.get("id")
            if not controller_id:
                continue
            if controller_id not in self._serial_helpers:
                raise LedDriverError("CAN controller interface is not connected")
            return controller_id
        raise LedDriverError("No controller is configured as the CAN interface")

    def _get_can_sender_id(self, controller_id: str) -> str:
        for controller in self._registry.get_controllers():
            if controller.get("id") == controller_id:
                sender_id = controller.get("can_sender_id")
                try:
                    return str(int(sender_id))
                except (TypeError, ValueError):
                    if sender_id is None:
                        return "0"
                    return str(sender_id)
        return "0"

    async def async_apply_group_action(self, group_id: str, action: str) -> dict[str, Any]:
        """Turn a group on/off and propagate to controllers."""
        _LOGGER.debug("Applying action %s to group %s", action, group_id)
        registry_group = self._registry.get_group(group_id)
        if registry_group is None:
            raise LedDriverError(f"Unknown group {group_id}")

        responses: dict[str, Any] = {}
        action_lower = action.lower()
        if action_lower == "off":
            channel_map = self._registry.build_group_channel_map(
                group_id,
                include_faulty=True,
            )
            if not channel_map:
                raise LedDriverError(f"No LEDs assigned to group {group_id}")

            for controller_id, drivers in channel_map.items():
                if controller_id not in self._serial_helpers:
                    raise LedDriverError(f"Controller {controller_id} not connected")
                message = {
                    "cm": "led",
                    "a": action,
                    "drvs": [
                        {"drv": driver_idx, "cs": channels}
                        for driver_idx, channels in sorted(drivers.items())
                    ],
                }
                await self._registry.async_append_serial_log(controller_id, direction="tx", payload=message)
                try:
                    await self._json_helper.async_send(controller_id, message)
                    responses[controller_id] = {"status": "queued"}
                except ValueError as err:
                    raise LedDriverError(str(err)) from err
                except SerialHelperError as err:
                    raise LedDriverError(str(err)) from err
            return responses

        pwm_map = self._registry.build_group_pwm_map(
            group_id,
            include_disabled=True,
            include_faulty=True,
            brightness=registry_group.get("brightness"),
        )
        if not pwm_map:
            raise LedDriverError(f"No LEDs assigned to group {group_id}")

        for controller_id, drivers in pwm_map.items():
            if controller_id not in self._serial_helpers:
                raise LedDriverError(f"Controller {controller_id} not connected")
            message = {
                "cm": "led",
                "a": action,
                "drvs": [
                    {
                        "drv": driver_idx,
                        "cs": slots,
                    }
                    for driver_idx, slots in sorted(drivers.items())
                ],
            }
            await self._registry.async_append_serial_log(controller_id, direction="tx", payload=message)
            try:
                await self._json_helper.async_send(controller_id, message)
                responses[controller_id] = {"status": "queued"}
            except ValueError as err:
                raise LedDriverError(str(err)) from err
            except SerialHelperError as err:
                raise LedDriverError(str(err)) from err

        return responses

    async def async_apply_group_pwm_targets(
        self,
        group_id: str,
        targets: dict[str, Any],
        brightness: int | None = None,
    ) -> dict[str, Any]:
        """Apply explicit PWM targets for each LED in a group, and optionally update brightness."""

        if not isinstance(targets, dict) or not targets:
            raise LedDriverError("PWM targets payload is empty")

        registry_group = self._registry.get_group(group_id)
        if registry_group is None:
            raise LedDriverError(f"Unknown group {group_id}")

        _LOGGER.debug(
            "Applying PWM targets for group %s (%s LEDs)",
            group_id,
            len(targets),
        )

        led_ids = registry_group.get("led_ids", [])
        resolved = self._registry.resolve_output_ids(led_ids)
        if not resolved:
            raise LedDriverError(f"No LEDs assigned to group {group_id}")

        updates: dict[str, dict[int, dict[int, int]]] = defaultdict(lambda: defaultdict(dict))
        matched = False

        for driver, output in resolved:
            output_id = output.get("id")
            if output_id not in targets:
                continue
            if output.get("disabled"):
                continue

            pwm_value = _to_int(targets[output_id])
            if pwm_value is None:
                continue

            min_pwm = int(output.get("min_pwm", 0))
            max_pwm = int(output.get("max_pwm", 255))
            if max_pwm < min_pwm:
                max_pwm = min_pwm
            pwm_value = max(min_pwm, min(max_pwm, pwm_value))

            controller_id = driver.get("controller_id")
            driver_index = _to_int(driver.get("driver_index", 0))
            if controller_id is None or driver_index is None:
                continue

            channels = output.get("channels") or []
            if not channels:
                slot_index = _to_int(output.get("slot"))
                if slot_index is not None:
                    channels = [slot_index]

            channel_indices: list[int] = []
            for channel in channels:
                channel_index = _to_int(channel)
                if channel_index is not None:
                    channel_indices.append(channel_index)

            if not channel_indices:
                continue

            for channel_index in channel_indices:
                updates[controller_id][driver_index][channel_index] = pwm_value

            output["target_pwm"] = pwm_value
            matched = True

        if not matched:
            raise LedDriverError("No matching LEDs for supplied PWM targets")

        responses = await self._dispatch_pwm_updates(updates, strict=True)

        # Update stored brightness if provided
        brightness_changed = False
        if brightness is not None:
            try:
                brightness_value = int(brightness)
            except (TypeError, ValueError):
                brightness_value = None
            if brightness_value is not None:
                brightness_value = max(0, min(100, brightness_value))
                if registry_group.get("brightness") != brightness_value:
                    registry_group["brightness"] = brightness_value
                    brightness_changed = True

        await self._registry.async_commit()
        if brightness_changed:
            await self._broadcast_group_updates({group_id})
        _LOGGER.debug(
            "Group %s PWM targets dispatched (controllers=%s)",
            group_id,
            list(responses),
        )
        return responses

    async def async_set_output_pwm(self, output_id: str, pwm: int) -> dict[str, Any]:
        """Set a single output PWM without changing group brightness."""

        resolved = self._registry.resolve_output_ids([output_id])
        if not resolved:
            raise LedDriverError(f"Unknown output {output_id}")

        driver, output = resolved[0]
        controller_id = driver.get("controller_id")
        driver_index = _to_int(driver.get("driver_index"))
        channels = output.get("channels") or []
        if not channels:
            slot_index = _to_int(output.get("slot"))
            if slot_index is not None:
                channels = [slot_index]
        if controller_id is None or driver_index is None or not channels:
            raise LedDriverError("Output is missing controller/driver/channel mapping")

        try:
            min_pwm = int(output.get("min_pwm", 0))
        except (TypeError, ValueError):
            min_pwm = 0
        try:
            max_pwm = int(output.get("max_pwm", 255))
        except (TypeError, ValueError):
            max_pwm = 255
        if max_pwm < min_pwm:
            max_pwm = min_pwm

        try:
            pwm_value = int(pwm)
        except (TypeError, ValueError):
            raise LedDriverError("Invalid PWM value") from None
        pwm_value = max(min_pwm, min(max_pwm, pwm_value))

        updates: dict[str, dict[int, dict[int, int]]] = defaultdict(lambda: defaultdict(dict))
        for channel in channels:
            channel_idx = _to_int(channel)
            if channel_idx is None:
                continue
            updates[controller_id][driver_index][channel_idx] = pwm_value

        responses = await self._dispatch_pwm_updates(updates, strict=True)

        output["pwm"] = pwm_value
        output["level"] = 1 if pwm_value > min_pwm else 0
        await self._registry.async_commit()

        changed_groups = self._update_groups_from_output_ids({output_id})
        if changed_groups:
            await self._broadcast_group_updates(changed_groups)

        return responses

    async def async_set_output_state(self, output_id: str, turn_on: bool) -> dict[str, Any]:
        """Turn a single output on/off without affecting group brightness."""

        resolved = self._registry.resolve_output_ids([output_id])
        if not resolved:
            raise LedDriverError(f"Unknown output {output_id}")

        driver, output = resolved[0]
        controller_id = driver.get("controller_id")
        driver_index = _to_int(driver.get("driver_index"))
        channels = output.get("channels") or []
        if not channels:
            slot_index = _to_int(output.get("slot"))
            if slot_index is not None:
                channels = [slot_index]
        if controller_id is None or driver_index is None or not channels:
            raise LedDriverError("Output is missing controller/driver/channel mapping")

        try:
            min_pwm = int(output.get("min_pwm", 0))
        except (TypeError, ValueError):
            min_pwm = 0
        try:
            max_pwm = int(output.get("max_pwm", 255))
        except (TypeError, ValueError):
            max_pwm = 255
        if max_pwm < min_pwm:
            max_pwm = min_pwm

        try:
            target_pwm = int(output.get("target_pwm", max_pwm))
        except (TypeError, ValueError):
            target_pwm = max_pwm
        target_pwm = max(min_pwm, min(max_pwm, target_pwm))

        responses: dict[str, Any] = {}
        if turn_on:
            slots = [-1, -1, -1, -1]
            for channel in channels:
                ch_idx = _to_int(channel)
                if ch_idx is not None and 0 <= ch_idx < len(slots):
                    slots[ch_idx] = target_pwm
            message = {
                "cm": "led",
                "a": "on",
                "drvs": [
                    {
                        "drv": driver_index,
                        "cs": slots,
                    }
                ],
            }
            pwm_value = target_pwm
        else:
            channel_list: list[int] = []
            for channel in channels:
                ch_idx = _to_int(channel)
                if ch_idx is not None:
                    channel_list.append(ch_idx)
            message = {
                "cm": "led",
                "a": "off",
                "drvs": [
                    {
                        "drv": driver_index,
                        "cs": channel_list,
                    }
                ],
            }
            pwm_value = 0

        await self._registry.async_append_serial_log(controller_id, direction="tx", payload=message)
        try:
            await self._json_helper.async_send(controller_id, message)
            responses[controller_id] = {"status": "queued"}
        except (ValueError, SerialHelperError) as err:
            raise LedDriverError(str(err)) from err

        # Update local state
        output["pwm"] = pwm_value
        output["level"] = 1 if turn_on and pwm_value > min_pwm else 0
        await self._registry.async_commit()

        changed_groups = self._update_groups_from_output_ids({output_id})
        if changed_groups:
            await self._broadcast_group_updates(changed_groups)

        return responses

    async def async_set_ssr_state(self, ssr_id: str, turn_on: bool) -> dict[str, Any]:
        """Toggle an SSR output via the CAN controller interface."""

        can_controller_id = self._get_can_controller_id()
        base_address = self._registry.get_ssr_base_address()
        if base_address <= 0:
            raise LedDriverError("Configure the SSR base address before sending commands")

        ssr_entries = self._registry.get_ssr_config().get("entries", {})
        entry = ssr_entries.get(ssr_id)
        if entry is None:
            raise LedDriverError(f"Unknown SSR entry {ssr_id}")
        bit_index = _to_int(entry.get("bit_index"))
        if bit_index is None or bit_index not in SSR_ALLOWED_BITS:
            raise LedDriverError("SSR entry is missing a valid bit index")

        mask = self._registry.get_ssr_state_mask()
        if turn_on:
            mask |= bit_index
        else:
            mask &= ~bit_index
        mask &= _SSR_MASK

        data = [
            base_address & 0xFF,
            0,
            3,
            (mask >> 8) & 0xFF,
            mask & 0xFF,
            0x33,
            0x22,
            0x11,
        ]
        message = {
            "cm": "can",
            "a": "send",
            "i": self._get_can_sender_id(can_controller_id),
            "d": data,
        }

        _LOGGER.debug(
            "SSR %s turn_on=%s base=%s mask=%s controller=%s",
            ssr_id,
            turn_on,
            base_address,
            mask,
            can_controller_id,
        )
        await self._registry.async_append_serial_log(can_controller_id, direction="tx", payload=message)
        try:
            await self._json_helper.async_send(can_controller_id, message)
        except (ValueError, SerialHelperError) as err:
            raise LedDriverError(str(err)) from err

        self._registry.set_ssr_entry_state(ssr_id, turn_on)
        await self._registry.async_commit()

        return {
            "controller_id": can_controller_id,
            "mask": mask,
        }

    async def async_apply_led_configs(self, configs: list[dict[str, Any]]) -> dict[str, Any]:
        """Send per-channel LED configuration updates."""

        if not isinstance(configs, list) or not configs:
            raise LedDriverError("No LED configs provided")

        responses: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for entry in configs:
            controller_id = entry.get("controller_id")
            driver_index = _to_int(entry.get("driver_index"))
            channel = _to_int(entry.get("channel"))
            min_pwm = _to_int(entry.get("min_pwm", entry.get("mip", 0)))
            max_pwm = _to_int(entry.get("max_pwm", entry.get("mp", 255)))
            current_high = bool(entry.get("current_high"))

            if not controller_id or driver_index is None or channel is None:
                raise LedDriverError("Invalid LED configuration payload")

            helper = self._serial_helpers.get(controller_id)
            if helper is None:
                raise LedDriverError(f"Controller {controller_id} not connected")

            if min_pwm is None:
                min_pwm = 0
            if max_pwm is None:
                max_pwm = 255

            message = {
                "cm": "led",
                "a": "cfg",
                "dvr": driver_index,
                "c": channel,
                "mip": min_pwm,
                "mp": max_pwm,
                "ch": current_high,
            }

            await self._registry.async_append_serial_log(controller_id, direction="tx", payload=message)
            try:
                await self._json_helper.async_send(controller_id, message)
            except (ValueError, SerialHelperError) as err:
                raise LedDriverError(str(err)) from err

            responses[controller_id].append(
                {
                    "driver_index": driver_index,
                    "channel": channel,
                }
            )

        return responses

    async def async_shutdown(self) -> None:
        if self._registry_listener is not None:
            self._registry.async_remove_listener(self._registry_listener)
            self._registry_listener = None
        for helper in self._serial_helpers.values():
            await helper.async_close()
        self._serial_helpers.clear()
        for task in list(self._json_helper.helper_tasks.values()):
            task.cancel()
        for task in list(self._json_helper.helper_tasks.values()):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._json_helper.helper_tasks.clear()
        self._json_helper.helpers.clear()
        self._listeners_registered = False
        for controller_id in list(self._poll_tasks):
            self._stop_polling(controller_id)
        self._poll_tasks.clear()
        self._poll_enabled.clear()
        for state in self._button_states.values():
            if state.ramp_task is not None:
                state.ramp_task.cancel()
        self._button_states.clear()
        self._switch_buttons.clear()
        self._switch_masks.clear()

    async def async_set_controller_poll(self, controller_id: str, enabled: bool) -> None:
        await self._registry.async_upsert_controller({"id": controller_id, "polling_enabled": enabled})
        controller = next((ctrl for ctrl in self._registry.get_controllers() if ctrl["id"] == controller_id), None)
        if controller is not None:
            self._apply_polling_state(controller_id, controller)

    def _apply_channel_state_event(self, controller_id: str, event: dict[str, Any]) -> set[str]:
        driver_index = _to_int(_get(event, "dvr", "driver", "idx"))
        channel = _to_int(_get(event, "c", "channel"))
        slot = _to_int(_get(event, "i", "id", "idx"))

        matches = self._match_outputs(controller_id, driver_index, channel, slot)
        if not matches:
            _LOGGER.debug(
                "Controller %s reported channel state but no outputs matched: %s",
                controller_id,
                event,
            )
            return set()

        changed_outputs: set[str] = set()
        state = (_get(event, "sa", "state") or "").lower()
        level_val = _to_int(_get(event, "level"))
        pwm_val = _to_int(_get(event, "pwm"))
        if level_val is None and _get_bool(event, "on") is not None:
            level_val = 1 if _get_bool(event, "on") else 0
        fault_flag = _get_bool(event, "flt", "fault")

        if level_val is None and state:
            level_val = 1 if state in {"on", "true", "1"} else 0

        for driver, output in matches:
            if output.get("disabled"):
                continue
            output_changed = False
            if level_val is not None and int(output.get("level", 0)) != level_val:
                output["level"] = level_val
                output_changed = True
            if pwm_val is not None and int(output.get("pwm", -1)) != pwm_val:
                output["pwm"] = pwm_val
                output_changed = True
            elif level_val is not None:
                if level_val:
                    max_pwm = output.get("max_pwm")
                    if max_pwm is not None and int(output.get("pwm", -1)) != int(max_pwm):
                        output["pwm"] = int(max_pwm)
                        output_changed = True
                else:
                    min_pwm = output.get("min_pwm", 0)
                    target_pwm = 0 if min_pwm == 0 else int(min_pwm)
                    if int(output.get("pwm", -1)) != target_pwm:
                        output["pwm"] = target_pwm
                        output_changed = True
            if fault_flag is not None and bool(output.get("faulty", False)) != bool(fault_flag):
                output["faulty"] = bool(fault_flag)
                output_changed = True

            if output_changed:
                changed_outputs.add(output["id"])

        return changed_outputs

    def _apply_fault_event(
        self,
        controller_id: str,
        event: dict[str, Any],
        is_fault: bool,
    ) -> set[str]:
        driver_index = _to_int(_get(event, "dvr", "driver", "idx"))
        channel = _to_int(_get(event, "c", "channel"))
        slot = _to_int(_get(event, "i", "id", "idx"))

        matches = self._match_outputs(controller_id, driver_index, channel, slot)
        if not matches:
            _LOGGER.debug(
                "Controller %s reported fault change but no outputs matched: %s",
                controller_id,
                event,
            )
            return set()

        changed_outputs: set[str] = set()
        for driver, output in matches:
            if output.get("disabled"):
                if output.get("faulty"):
                    output["faulty"] = False
                    changed_outputs.add(output["id"])
                continue
            if bool(output.get("faulty", False)) != is_fault:
                output["faulty"] = is_fault
                changed_outputs.add(output["id"])

        return changed_outputs

    def _match_outputs(
        self,
        controller_id: str,
        driver_index: int | None,
        channel: int | None,
        slot: int | None,
    ) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        channel_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        slot_matches: list[tuple[dict[str, Any], dict[str, Any]]] = []

        for driver in self._registry.get_drivers():
            if driver.get("controller_id") != controller_id:
                continue
            if driver_index is not None:
                try:
                    if int(driver.get("driver_index", 0)) != driver_index:
                        continue
                except (TypeError, ValueError):
                    continue

            for output in driver.get("outputs", []):
                if output.get("disabled"):
                    continue
                channels = output.get("channels", []) or []
                if channel is not None and channel in channels:
                    channel_matches.append((driver, output))
                output_slot = _to_int(output.get("slot"))
                if slot is not None and output_slot is not None and output_slot == slot:
                    slot_matches.append((driver, output))

        return channel_matches or slot_matches

    def _update_groups_from_output_ids(self, output_ids: set[str]) -> set[str]:
        if not output_ids:
            return set()

        changed_groups: set[str] = set()
        for group in self._registry.get_groups():
            led_ids = group.get("led_ids", []) or []
            if not set(led_ids).intersection(output_ids):
                continue

            resolved = self._registry.resolve_output_ids(led_ids)
            active_outputs = [(driver, output) for driver, output in resolved if not output.get("disabled")]
            if not active_outputs:
                is_on = False
            else:
                # Group is considered on only if every active child is either on (level>0)
                # or faulted. This avoids prematurely marking a shared LED's other groups on/off.
                is_on = all((output.get("level", 0) > 0) or output.get("faulty") for _, output in active_outputs)

            if bool(group.get("is_on", False)) != is_on:
                group["is_on"] = is_on
                changed_groups.add(group["id"])

        return changed_groups

    async def _broadcast_group_updates(self, group_ids: set[str]) -> None:
        for group_id in group_ids:
            group = self._registry.get_group(group_id)
            if group is None:
                continue
            payload = {
                "group_id": group_id,
                "is_on": bool(group.get("is_on")),
                "brightness": group.get("brightness"),
            }
            self._hass.bus.async_fire("s2j_led_driver_group_state", payload)

    def _apply_polling_state(self, controller_id: str, controller: dict[str, Any]) -> None:
        if controller.get("polling_enabled"):
            self._start_polling(controller_id)
        else:
            self._stop_polling(controller_id)

    def _sync_buttons(self) -> None:
        new_states: dict[tuple[int, int], _ButtonState] = {}
        switch_map: dict[int, list[_ButtonState]] = defaultdict(list)

        switches = self._registry.get_switches()
        switches_by_id = {switch["id"]: switch for switch in switches}
        switches_by_value: dict[int, dict[str, Any]] = {}
        for switch_entry in switches:
            try:
                value = int(switch_entry.get("switch", 0))
            except (TypeError, ValueError):
                continue
            switches_by_value[value] = switch_entry

        buttons = self._registry.get_buttons()
        for button in buttons:
            try:
                switch = int(button.get("switch", 0))
                mask = int(button.get("mask", 0))
            except (TypeError, ValueError):
                continue
            if mask <= 0:
                continue
            key = (switch, mask)
            switch_record = switches_by_id.get(button.get("switch_id")) or switches_by_value.get(switch)
            button_count = _extract_button_count(switch_record)
            state = self._button_states.get(key)
            if state is None:
                state = _ButtonState(
                    switch=switch,
                    mask=mask,
                    group_id=button.get("group_id"),
                    name=button.get("name"),
                    button_count=button_count,
                )
            else:
                state.group_id = button.get("group_id")
                state.name = button.get("name")
                state.button_count = button_count
            state.metadata = button.get("metadata", {})
            new_states[key] = state
            switch_map[switch].append(state)

        # cancel tasks for removed buttons
        for key, state in self._button_states.items():
            if key not in new_states and state.ramp_task is not None:
                state.ramp_task.cancel()

        self._button_states = new_states
        self._switch_buttons = switch_map
        for switch in list(self._switch_masks.keys()):
            if switch not in self._switch_buttons:
                self._switch_masks.pop(switch, None)

    async def _handle_status_response(self, controller_id: str, response: dict[str, Any]) -> None:
        if not isinstance(response, dict):
            return
        await self._registry.async_append_serial_log(controller_id, direction="rx", payload=response)
        if _get(response, "t", "type") != "status":
            return

        changed_outputs, changed = self._apply_status_snapshot(controller_id, response)
        changed_groups: set[str] = set()
        if changed_outputs:
            updated = self._update_groups_from_output_ids(changed_outputs)
            changed_groups.update(updated)
            if updated:
                changed = True

        if changed:
            await self._registry.async_commit()
            if changed_groups:
                await self._broadcast_group_updates(changed_groups)

    async def _handle_button_event(self, controller_id: str, event: dict[str, Any]) -> None:
        data = _get(event, "d", "data")
        if not isinstance(data, list) or len(data) < 6:
            return
        try:
            switch = int(data[0])
            mask = int(data[5])
        except (TypeError, ValueError):
            return

        _LOGGER.debug(
            "Controller %s button event switch=%s mask=%s previous=%s",
            controller_id,
            switch,
            mask,
            self._switch_masks.get(switch, 0),
        )

        buttons = self._switch_buttons.get(switch)
        previous_mask = self._switch_masks.get(switch, 0)
        self._switch_masks[switch] = mask

        learn_bits = mask & ~previous_mask
        if learn_bits:
            known_masks = {state.mask for state in buttons or []}
            for bit in _iter_button_bits(learn_bits):
                if bit not in known_masks:
                    _LOGGER.debug(
                        "Controller %s learned button switch=%s mask=%s",
                        controller_id,
                        switch,
                        bit,
                    )
                    await self._registry.async_record_learned_switch(
                        controller_id=controller_id,
                        switch=switch,
                        mask=bit,
                    )

        if not buttons:
            return

        now = monotonic()

        for button_state in buttons:
            pressed = bool(mask & button_state.mask)
            was_pressed = bool(previous_mask & button_state.mask)

            if pressed and not was_pressed:
                await self._handle_button_press(button_state, now)
            elif not pressed and was_pressed:
                await self._handle_button_release(button_state, now)
            elif pressed:
                await self._handle_button_hold(button_state, now)

    def _apply_status_snapshot(
        self,
        controller_id: str,
        status: dict[str, Any],
    ) -> tuple[set[str], bool]:
        changed_outputs: set[str] = set()
        changed = False

        registry_data = self._registry.data
        controllers = registry_data.get("controllers", {})
        controller = controllers.get(controller_id)

        if controller is not None:
            metadata = dict(controller.get("metadata", {}))
            status_meta = {
                "uptime_ms": _get(status, "um", "uptime_ms"),
                "drivers_reported": _get(status, "drvs", "drivers"),
                "device_name": _get(status, "dv", "device_name"),
                "fan": self._normalize_fan(_get(status, "f", "fan")),
            }
            acs_status = _get(status, "ac", "acs") or {}
            acs_sensors = _get(acs_status, "sns", "sensors") or []

            def _extract_kalman(block: dict[str, Any] | None) -> float | None:
                if not isinstance(block, dict):
                    return None
                value = block.get("k") if "k" in block else block.get("kalman")
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None

            acs_summary: list[dict[str, Any]] = []
            total_power = 0.0
            total_current = 0.0
            total_voltage = 0.0
            voltage_samples = 0

            acs_history = dict(metadata.get("acs_history", {}))

            for sensor in acs_sensors:
                bus = _get(sensor, "bu", "bus") or f"Bus {sensor.get('index', '?')}"
                sample = _get(sensor, "s", "sample") or {}
                ready = bool(_get_bool(sensor, "rd", "ready") or False)
                valid = bool(_get_bool(sample, "vd", "valid") or False)
                voltage = _extract_kalman(_get(sample, "vlt", "voltage"))
                current = _extract_kalman(_get(sample, "cu", "current"))
                power = _extract_kalman(_get(sample, "pw", "power"))

                acs_summary.append(
                    {
                        "bus": bus,
                        "index": _get(sensor, "idx", "index"),
                        "ready": ready,
                        "valid": valid,
                        "voltage": voltage,
                        "current": current,
                        "power": power,
                    }
                )

                if valid:
                    if power is not None:
                        total_power += power
                    if current is not None:
                        total_current += current
                    if voltage is not None:
                        total_voltage += voltage
                        voltage_samples += 1

                    record = {
                        "timestamp_ms": _get(sample, "um", "updated_ms"),
                        "voltage": voltage,
                        "current": current,
                        "power": power,
                    }
                    history = acs_history.setdefault(bus, [])
                    history.append(record)
                    if len(history) > 200:
                        del history[: len(history) - 200]

            status_meta["acs"] = acs_summary
            status_meta["total_power"] = total_power
            status_meta["total_current"] = total_current
            status_meta["total_voltage"] = (total_voltage / voltage_samples) if voltage_samples else None
            status_meta["voltage_sample_count"] = voltage_samples

            if metadata.get("acs_history") != acs_history:
                metadata["acs_history"] = acs_history
                changed = True

            if metadata.get("status") != status_meta:
                metadata["status"] = status_meta
                controller["metadata"] = metadata
                changed = True
            elif metadata is not controller.get("metadata"):
                controller["metadata"] = metadata

        drivers = registry_data.get("drivers", {})
        led_status = _get(status, "l", "led") or {}
        status_drivers = _get(led_status, "drvs", "drivers") or []

        for status_driver in status_drivers:
            try:
                status_index = int(_get(status_driver, "idx", "index"))
            except (TypeError, ValueError):
                continue

            matched_driver = None
            for driver in drivers.values():
                if driver.get("controller_id") != controller_id:
                    continue
                try:
                    if int(driver.get("driver_index", 0)) != status_index:
                        continue
                except (TypeError, ValueError):
                    continue
                matched_driver = driver
                break

            if matched_driver is None:
                continue

            driver_meta = dict(matched_driver.get("metadata", {}))
            driver_status_meta = {
                "available": _get_bool(status_driver, "av", "available"),
                "indicator": _get_bool(status_driver, "idc", "indicator"),
                "wire": _get(status_driver, "w", "wire"),
            }
            if driver_meta.get("status") != driver_status_meta:
                driver_meta["status"] = driver_status_meta
                matched_driver["metadata"] = driver_meta
                changed = True

            outputs = matched_driver.get("outputs", []) or []
            outputs_by_slot = {
                int(output.get("slot", idx)): output
                for idx, output in enumerate(outputs)
            }

            for channel in _get(status_driver, "cs", "channels") or []:
                try:
                    slot = int(_get(channel, "idx", "index"))
                except (TypeError, ValueError):
                    continue
                output = outputs_by_slot.get(slot)
                if output is None:
                    continue

                output_changed = False
                on_flag = _get_bool(channel, "on")
                if on_flag is None:
                    state_val = (_get(channel, "sa", "state") or "").lower()
                    new_level = 1 if state_val in {"on", "true", "1"} else 0
                else:
                    new_level = 1 if on_flag else 0
                if int(output.get("level", 0)) != new_level:
                    output["level"] = new_level
                    output_changed = True

                try:
                    new_pwm = int(_get(channel, "pwm") or 0)
                except (TypeError, ValueError):
                    new_pwm = 0
                if int(output.get("pwm", 0)) != new_pwm:
                    output["pwm"] = new_pwm
                    output_changed = True

                new_fault = bool(
                    _get_bool(channel, "flt", "fault")
                    or _get_bool(channel, "er", "errored")
                    or _get_bool(channel, "ch", "current_high")
                )
                if bool(output.get("faulty", False)) != new_fault:
                    output["faulty"] = new_fault
                    output_changed = True

                if output_changed:
                    changed = True
                    changed_outputs.add(output["id"])

        return changed_outputs, changed

    @staticmethod
    def _normalize_fan(fan: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(fan, dict):
            return None
        return {
            "ready": _get_bool(fan, "rd", "ready"),
            "count": _get(fan, "cn", "count"),
            "rpm": _get(fan, "rpm"),
            "fault": _get_bool(fan, "flt", "fault"),
        }

    async def _handle_button_press(self, button_state: _ButtonState, now: float) -> None:
        button_state.pressed = True
        button_state.hold_started = now
        double_press = (
            button_state.last_release
            and (now - button_state.last_release) <= _SWITCH_DOUBLE_PRESS_WINDOW
        )
        button_state.last_press = now
        if double_press:
            button_state.direction *= -1
            _LOGGER.debug(
                "Button %s switch=%s mask=%s double-press, new direction=%s",
                button_state.group_id,
                button_state.switch,
                button_state.mask,
                button_state.direction,
            )
        elif button_state.group_id:
            _LOGGER.debug(
                "Button %s switch=%s mask=%s toggling group",
                button_state.group_id,
                button_state.switch,
                button_state.mask,
            )
            await self._toggle_group(button_state.group_id)

    async def _handle_button_release(self, button_state: _ButtonState, now: float) -> None:
        button_state.pressed = False
        button_state.last_release = now
        button_state.hold_started = 0.0
        if button_state.ramp_task is not None:
            button_state.ramp_task.cancel()
            button_state.ramp_task = None

    async def _handle_button_hold(self, button_state: _ButtonState, now: float) -> None:
        if not button_state.group_id:
            return

        if button_state.ramp_task is not None and not button_state.ramp_task.done():
            return

        if now - button_state.hold_started < _SWITCH_HOLD_THRESHOLD:
            return

        if button_state.ramp_task is None:
            button_state.ramp_task = self._hass.loop.create_task(self._run_pwm_ramp(button_state))

    async def _toggle_group(self, group_id: str) -> None:
        group = self._registry.get_group(group_id)
        if group is None:
            return
        is_on = bool(group.get("is_on"))
        action = "off" if is_on else "on"
        try:
            _LOGGER.debug("Group %s toggle action=%s", group_id, action)
            await self.async_apply_group_action(group_id, action)
            group = self._registry.get_group(group_id)
        except LedDriverError:
            _LOGGER.debug("Failed to toggle group %s", group_id, exc_info=True)

    async def _run_pwm_ramp(self, button_state: _ButtonState) -> None:
        try:
            await self._ensure_group_on(button_state.group_id)
            while button_state.pressed:
                updated = await self._step_group_pwm(button_state.group_id, button_state.direction)
                if not updated:
                    break
                await asyncio.sleep(_PWM_STEP_INTERVAL)
        except asyncio.CancelledError:  # pragma: no cover
            raise
        finally:
            button_state.ramp_task = None

    async def _ensure_group_on(self, group_id: str) -> None:
        group = self._registry.get_group(group_id)
        if group is None:
            return
        if not group.get("is_on"):
            try:
                await self.async_apply_group_action(group_id, "on")
            except LedDriverError:
                _LOGGER.debug("Failed to turn on group %s", group_id, exc_info=True)

    async def _step_group_pwm(self, group_id: str, direction: int) -> bool:
        group = self._registry.get_group(group_id)
        if group is None:
            return False

        led_ids = group.get("led_ids", [])
        resolved = self._registry.resolve_output_ids(led_ids)
        if not resolved:
            return False

        updates: dict[str, dict[int, dict[int, int]]] = defaultdict(lambda: defaultdict(dict))
        changed = False

        for driver, output in resolved:
            if output.get("disabled"):
                continue
            pwm = int(output.get("pwm", 0))
            min_pwm = int(output.get("min_pwm", 0))
            max_pwm = int(output.get("max_pwm", 255))
            step = _PWM_STEP_SIZE if direction >= 0 else -_PWM_STEP_SIZE
            target = pwm + step
            if direction >= 0:
                target = min(target, max_pwm)
            else:
                target = max(target, min_pwm)
            if target == pwm:
                continue
            output["pwm"] = target
            output["level"] = 1 if target > min_pwm else 0
            changed = True
            controller_id = driver.get("controller_id")
            driver_index = int(driver.get("driver_index", 0))
            channels = output.get("channels") or [output.get("slot")]
            for channel_index in channels:
                updates[controller_id][driver_index][int(channel_index)] = target

        if not changed:
            return False

        await self._dispatch_pwm_updates(updates)
        await self._registry.async_commit()
        return True

    async def _dispatch_pwm_updates(
        self,
        updates: dict[str, dict[int, dict[int, int]]],
        *,
        strict: bool = False,
    ) -> dict[str, Any]:
        responses: dict[str, Any] = {}
        for controller_id, driver_map in updates.items():
            if controller_id not in self._serial_helpers:
                message = f"Skipping PWM update for unknown controller {controller_id}"
                if strict:
                    raise LedDriverError(message)
                _LOGGER.debug(message)
                continue

            pwm_map: dict[int, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
            for driver_index, channels in driver_map.items():
                for channel_index, pwm in channels.items():
                    pwm_map[pwm][driver_index].add(channel_index)

            controller_responses: dict[int, str] = {}
            _LOGGER.debug(
                "Dispatching PWM updates controller=%s batches=%s",
                controller_id,
                len(pwm_map),
            )
            for pwm, driver_channels in pwm_map.items():
                message = {
                    "cm": "led",
                    "a": "pwm",
                    "drvs": [
                        {"dvr": driver_index, "cs": sorted(channel_set)}
                        for driver_index, channel_set in sorted(driver_channels.items())
                    ],
                    "pwm": pwm,
                }
                try:
                    await self._registry.async_append_serial_log(controller_id, direction="tx", payload=message)
                    await self._json_helper.async_send(controller_id, message)
                    controller_responses[pwm] = "queued"
                    _LOGGER.debug(
                        "Controller %s PWM command queued pwm=%s drivers=%s",
                        controller_id,
                        pwm,
                        len(driver_channels),
                    )
                except (ValueError, SerialHelperError) as err:
                    if strict:
                        raise LedDriverError(str(err)) from err
                    _LOGGER.debug("Controller %s pwm update failed: %s", controller_id, err)
            if controller_responses:
                responses[controller_id] = controller_responses
        return responses

    def _start_polling(self, controller_id: str) -> None:
        if controller_id in self._poll_tasks:
            return

        self._poll_enabled.add(controller_id)

        async def _poll_loop() -> None:
            try:
                while controller_id in self._poll_enabled:
                    if controller_id in self._serial_helpers:
                        message = {"cm": "status"}
                        try:
                            await self._registry.async_append_serial_log(controller_id, direction="tx", payload=message)
                            await self._json_helper.async_send(controller_id, message)
                        except (ValueError, SerialHelperError) as err:
                            _LOGGER.debug("Controller %s status poll failed: %s", controller_id, err)
                    await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:  # pragma: no cover - task cancellation
                raise
            finally:
                self._poll_tasks.pop(controller_id, None)
                self._poll_enabled.discard(controller_id)

        task = self._hass.loop.create_task(_poll_loop())
        self._poll_tasks[controller_id] = task

    def _stop_polling(self, controller_id: str) -> None:
        self._poll_enabled.discard(controller_id)
        task = self._poll_tasks.pop(controller_id, None)
        if task is not None:
            task.cancel()


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iter_button_bits(mask: Any) -> list[int]:
    bits: list[int] = []
    try:
        value = int(mask)
    except (TypeError, ValueError):
        return bits
    if value < 0:
        return bits
    while value:
        bit = value & -value
        bits.append(bit)
        value ^= bit
    return bits
