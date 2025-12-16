"""Persistent registry for LED controllers, drivers, and groups."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from copy import deepcopy
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

STORAGE_KEY = "s2j_led_driver_registry"
STORAGE_VERSION = 1

TOTAL_OUTPUTS_PER_DRIVER = 4
SSR_MAX_ENTRIES = 10
SSR_MAX_BITS = 10
PATCH_PANEL_PORTS = 48

RegistryListener = Callable[[], None]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _normalize_channels(channels: Iterable[Any]) -> list[int]:
    unique: set[int] = set()
    for channel in channels or []:
        try:
            unique.add(int(channel))
        except (TypeError, ValueError):
            continue
    return sorted(unique)


def _normalize_output_ids(output_ids: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for output_id in output_ids or []:
        if not output_id:
            continue
        value = str(output_id).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _normalize_metadata(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalize_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _normalize_button_count(value: Any, default: int = 5) -> int:
    """Clamp a button count to the supported range."""

    try:
        count = int(value)
    except (TypeError, ValueError):
        return default

    if count < 1:
        return 1
    if count > 5:
        return 5
    return count


def _get_switch_button_count(value: Any, *, default: int = 5) -> int:
    """Extract a switch button count from either the root or metadata."""

    if isinstance(value, dict):
        if "button_count" in value:
            return value.get("button_count", default)
        metadata = value.get("metadata")
        if isinstance(metadata, dict):
            return metadata.get("button_count", default)
    return default


def _normalize_switch_type(value: Any) -> str:
    if not value:
        return "momentary"
    return str(value).strip()


def _validate_ssr_bit(value: Any) -> int:
    try:
        bit = int(value)
    except (TypeError, ValueError):
        raise ValueError("SSR bit index must be an integer") from None
    if bit < 0 or bit >= SSR_MAX_BITS:
        raise ValueError(f"SSR bit index must be between 0 and {SSR_MAX_BITS - 1}")
    return bit


def _mask_to_index(mask: int) -> int:
    if mask <= 0 or (mask & (mask - 1)) != 0:
        return 0
    index = 0
    while mask > 1:
        mask >>= 1
        index += 1
    return index + 1


class LedRegistry:
    """Manage persisted controller, driver, and group definitions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store = Store[dict[str, Any]](hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, dict[str, Any]] = {
            "controllers": {},
            "drivers": {},
            "groups": {},
            "switches": {},
            "buttons": {},
            "learned_buttons": {},
            "ssr": {
                "base_address": 0,
                "entries": {},
            },
            "patch_panel": {
                "ports": {},
            },
        }
        self._listeners: list[RegistryListener] = []
        self._save_lock = asyncio.Lock()

    # General persistence helpers -------------------------------------------------

    @property
    def data(self) -> dict[str, dict[str, Any]]:
        return self._data

    async def async_load(self) -> None:
        migrated = False
        if (loaded := await self._store.async_load()) is not None:
            self._data = loaded
            migrated = self._migrate_legacy_data()
        if migrated:
            await self.async_save()

    async def async_save(self) -> None:
        async with self._save_lock:
            await self._store.async_save(self._data)

    async def async_commit(self) -> None:
        await self.async_save()
        self._async_notify()

    @callback
    def async_add_listener(self, listener: RegistryListener) -> None:
        self._listeners.append(listener)

    @callback
    def async_remove_listener(self, listener: RegistryListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    @callback
    def _async_notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    def _migrate_legacy_data(self) -> bool:
        """Convert data stored under earlier schema revisions."""

        changed = False
        data = self._data

        if "controllers" not in data:
            data["controllers"] = {}
            changed = True
        if "drivers" not in data:
            data["drivers"] = {}
            changed = True
        if "groups" not in data:
            data["groups"] = {}
            changed = True
        if "switches" not in data:
            data["switches"] = {}
            changed = True
        if "buttons" not in data:
            data["buttons"] = {}
            changed = True
        if "learned_buttons" not in data:
            data["learned_buttons"] = {}
            changed = True
        if "ssr" not in data or not isinstance(data["ssr"], dict):
            data["ssr"] = {"base_address": 0, "entries": {}}
            changed = True
        else:
            ssr = data["ssr"]
            if "base_address" not in ssr:
                ssr["base_address"] = 0
                changed = True
            if "entries" not in ssr or not isinstance(ssr["entries"], dict):
                ssr["entries"] = {}
                changed = True
        if "patch_panel" not in data or not isinstance(data["patch_panel"], dict):
            data["patch_panel"] = {"ports": {}}
            changed = True
        else:
            patch_panel = data["patch_panel"]
            if "ports" not in patch_panel or not isinstance(patch_panel["ports"], dict):
                patch_panel["ports"] = {}
                changed = True

        switch_lookup: dict[int, str] = {}
        for switch_entry in data["switches"].values():
            metadata = _normalize_metadata(switch_entry.get("metadata", {}))
            switch_entry["metadata"] = metadata
            switch_entry["type"] = _normalize_switch_type(switch_entry.get("type"))
            switch_entry["button_count"] = _normalize_button_count(switch_entry.get("button_count", 5))
            switch_entry["has_buzzer"] = _normalize_bool(switch_entry.get("has_buzzer", False))
            switch_entry["flash_leds"] = _normalize_bool(switch_entry.get("flash_leds", True))
            try:
                hardware = int(switch_entry.get("switch", 0))
            except (TypeError, ValueError):
                hardware = 0
            switch_entry["switch"] = hardware
            switch_lookup[hardware] = switch_entry["id"]

        inferred_counts: dict[str, int] = {}

        for button in data["buttons"].values():
            metadata = _normalize_metadata(button.get("metadata", {}))
            if "button_count" in metadata:
                metadata.pop("button_count", None)
                changed = True
            button["metadata"] = metadata

            try:
                hardware = int(button.get("switch", button.get("housing", 0)))
            except (TypeError, ValueError):
                hardware = 0
            button["switch"] = hardware

            switch_id = button.get("switch_id")
            if switch_id not in data["switches"]:
                switch_id = switch_lookup.get(hardware)
            if switch_id is None:
                switch_id = _new_id("switch")
                data["switches"][switch_id] = {
                    "id": switch_id,
                    "name": f"Switch {hardware}",
                    "switch": hardware,
                    "type": "momentary",
                    "button_count": 5,
                    "has_buzzer": False,
                    "flash_leds": True,
                    "metadata": {},
                }
                switch_lookup[hardware] = switch_id
                changed = True

            button["switch_id"] = switch_id
            if "housing" in button:
                button.pop("housing", None)
                changed = True
            if "button_count" in button:
                button.pop("button_count", None)
                changed = True

            inferred = inferred_counts.setdefault(switch_id, 0)
            inferred_counts[switch_id] = max(
                inferred,
                _mask_to_index(int(button.get("mask", 0))),
            )

        for switch_id, switch_entry in data["switches"].items():
            inferred = inferred_counts.get(switch_id, 0)
            normalized = _normalize_button_count(switch_entry.get("button_count", inferred or 5))
            if inferred:
                normalized = max(normalized, inferred)
            if switch_entry.get("button_count") != normalized:
                switch_entry["button_count"] = normalized
                changed = True

        legacy_leds = data.pop("leds", None)
        if not legacy_leds:
            return changed

        changed = True
        grouped: dict[tuple[Any, int], list[tuple[str, dict[str, Any]]]] = {}
        for led_id, led in legacy_leds.items():
            controller_id = led.get("controller_id")
            try:
                driver_index = int(led.get("driver", 0))
            except (TypeError, ValueError):
                driver_index = 0
            grouped.setdefault((controller_id, driver_index), []).append((led_id, deepcopy(led)))

        for (controller_id, driver_index), items in grouped.items():
            items.sort(key=lambda item: item[0])
            for chunk_index in range(0, len(items), TOTAL_OUTPUTS_PER_DRIVER):
                chunk = items[chunk_index : chunk_index + TOTAL_OUTPUTS_PER_DRIVER]
                driver_id = _new_id("drv")
                base_name = chunk[0][1].get("name") or f"Driver {driver_index}"
                if chunk_index:
                    driver_name = f"{base_name} ({chunk_index + 1})"
                else:
                    driver_name = base_name

                outputs = []
                for slot, (led_id, led) in enumerate(chunk):
                    outputs.append(
                        {
                            "id": led_id,
                            "slot": slot,
                            "name": led.get("name") or f"LED {slot + 1}",
                            "channels": _normalize_channels(led.get("channels", [])),
                            "faulty": bool(led.get("faulty", False)),
                            "pwm": int(led.get("pwm", 0)),
                            "level": int(led.get("level", 0)),
                            "min_pwm": int(led.get("min_pwm", 0)),
                            "max_pwm": int(led.get("max_pwm", 255)),
                        }
                    )

                driver_record = {
                    "id": driver_id,
                    "name": driver_name,
                    "controller_id": controller_id,
                    "driver_index": driver_index,
                }
                driver_record["outputs"] = self._normalize_driver_outputs(driver_id, outputs, [])
                self._data["drivers"][driver_id] = driver_record

        for group in self._data["groups"].values():
            group["led_ids"] = _normalize_output_ids(group.get("led_ids", []))

        return changed

    # Controllers -----------------------------------------------------------------

    def get_controllers(self) -> list[dict[str, Any]]:
        return list(self._data["controllers"].values())

    async def async_upsert_controller(self, controller: dict[str, Any]) -> dict[str, Any]:
        controller_id = controller.get("id") or _new_id("ctrl")
        stored = self._data["controllers"].get(
            controller_id,
            {
                "id": controller_id,
                "name": controller.get("name") or controller_id,
                "port": controller.get("port"),
                "baudrate": int(controller.get("baudrate", 115200)),
                "metadata": _normalize_metadata(controller.get("metadata", {})),
                "polling_enabled": bool(controller.get("polling_enabled", False)),
                "has_can_interface": _normalize_bool(
                    controller.get("has_can_interface", controller.get("can_interface", False))
                ),
            },
        )

        stored.update(
            {
                "name": controller.get("name", stored.get("name", controller_id)),
                "port": controller.get("port", stored.get("port")),
                "baudrate": int(controller.get("baudrate", stored.get("baudrate", 115200))),
                "metadata": _normalize_metadata(controller.get("metadata", stored.get("metadata", {}))),
                "polling_enabled": bool(controller.get("polling_enabled", stored.get("polling_enabled", False))),
                "has_can_interface": _normalize_bool(
                    controller.get(
                        "has_can_interface",
                        controller.get("can_interface", stored.get("has_can_interface", False)),
                    )
                ),
            }
        )

        self._data["controllers"][controller_id] = stored

        if stored.get("has_can_interface"):
            for other_id, other_controller in self._data["controllers"].items():
                if other_id == controller_id:
                    continue
                if other_controller.get("has_can_interface"):
                    other_controller["has_can_interface"] = False

        await self.async_commit()
        return stored

    # Switches ------------------------------------------------------------------

    def get_switches(self) -> list[dict[str, Any]]:
        return list(self._data["switches"].values())

    async def async_upsert_switch(self, switch: dict[str, Any]) -> dict[str, Any]:
        switch_id = switch.get("id") or _new_id("switch")
        stored = self._data["switches"].get(
            switch_id,
            {
                "id": switch_id,
                "name": switch.get("name") or f"Switch {switch.get('switch', switch_id)}",
                "switch": int(switch.get("switch", 0)),
                "type": _normalize_switch_type(switch.get("type")),
                "button_count": _normalize_button_count(switch.get("button_count", 5)),
                "has_buzzer": _normalize_bool(switch.get("has_buzzer", False)),
                "flash_leds": _normalize_bool(switch.get("flash_leds", True)),
                "metadata": _normalize_metadata(switch.get("metadata", {})),
            },
        )

        new_switch_value = int(switch.get("switch", stored.get("switch", 0)))
        stored.update(
            {
                "name": switch.get("name", stored.get("name", f"Switch {new_switch_value}")),
                "switch": new_switch_value,
                "type": _normalize_switch_type(switch.get("type", stored.get("type"))),
                "button_count": _normalize_button_count(
                    switch.get("button_count", stored.get("button_count", 5))
                ),
                "has_buzzer": _normalize_bool(
                    switch.get("has_buzzer", stored.get("has_buzzer", False))
                ),
                "flash_leds": _normalize_bool(
                    switch.get("flash_leds", stored.get("flash_leds", True))
                ),
                "metadata": _normalize_metadata(switch.get("metadata", stored.get("metadata", {}))),
            }
        )

        self._data["switches"][switch_id] = stored

        # Ensure child buttons reference the updated hardware value.
        for button in self._data["buttons"].values():
            if button.get("switch_id") == switch_id:
                button["switch"] = stored["switch"]

        await self.async_commit()
        return stored

    async def async_delete_switch(self, switch_id: str) -> None:
        if switch_id not in self._data["switches"]:
            return

        switch_entry = self._data["switches"].pop(switch_id)
        removed_masks: list[int] = []
        removed_buttons: list[str] = []
        for button_id, button in list(self._data["buttons"].items()):
            if button.get("switch_id") == switch_id:
                removed_masks.append(int(button.get("mask", 0)))
                removed_buttons.append(button_id)
                self._data["buttons"].pop(button_id, None)

        for mask in removed_masks:
            self._remove_learned_switch_entry(switch_entry.get("switch"), mask)

        await self.async_commit()

    # Buttons -------------------------------------------------------------------

    def get_buttons(self) -> list[dict[str, Any]]:
        return list(self._data["buttons"].values())

    def get_learned_buttons(self) -> list[dict[str, Any]]:
        return list(self._data["learned_buttons"].values())

    async def async_upsert_button(self, button: dict[str, Any]) -> dict[str, Any]:
        switch_id = button.get("switch_id")
        parent: dict[str, Any] | None = None
        if switch_id:
            parent = self._data["switches"].get(switch_id)

        if parent is None:
            try:
                switch_value = int(button.get("switch"))
            except (TypeError, ValueError):
                switch_value = None
            if switch_value is not None:
                parent = next(
                    (item for item in self._data["switches"].values() if int(item.get("switch", 0)) == switch_value),
                    None,
                )
                if parent:
                    switch_id = parent["id"]

        if parent is None or switch_id is None:
            raise ValueError("Button must reference an existing switch")

        mask = int(button.get("mask", 0))
        if mask <= 0 or (mask & (mask - 1)) != 0:
            raise ValueError("Button mask must be a single-bit value greater than zero")

        button_id = button.get("id") or _new_id("btn")
        is_new = button_id not in self._data["buttons"]

        siblings = [
            existing
            for existing in self._data["buttons"].values()
            if existing.get("switch_id") == switch_id and existing.get("id") != button_id
        ]

        if any(int(existing.get("mask", 0)) == mask for existing in siblings):
            raise ValueError("Button mask already assigned for this switch")

        if is_new:
            limit = parent.get("button_count", 5)
            if len(siblings) >= limit:
                raise ValueError("Configured button limit reached for this switch")

        stored = self._data["buttons"].get(
            button_id,
            {
                "id": button_id,
                "name": button.get("name") or f"{parent.get('name') or 'Switch'} Button",
                "switch_id": switch_id,
                "switch": int(parent.get("switch", 0)),
                "mask": mask,
                "group_id": button.get("group_id"),
                "metadata": _normalize_metadata(button.get("metadata", {})),
            },
        )

        metadata = _normalize_metadata(button.get("metadata", stored.get("metadata", {})))
        if "button_count" in metadata:
            metadata.pop("button_count", None)

        stored.update(
            {
                "name": button.get("name", stored.get("name", f"{parent.get('name') or 'Switch'} Button")),
                "switch_id": switch_id,
                "switch": int(parent.get("switch", stored.get("switch", 0))),
                "mask": mask,
                "group_id": button.get("group_id", stored.get("group_id")),
                "metadata": metadata,
            }
        )

        self._data["buttons"][button_id] = stored
        self._remove_learned_switch_entry(stored.get("switch"), stored.get("mask"))
        await self.async_commit()
        return stored

    async def async_delete_button(self, button_id: str) -> None:
        button = self._data["buttons"].pop(button_id, None)
        if button is None:
            return

        self._remove_learned_switch_entry(button.get("switch"), button.get("mask"))
        await self.async_commit()

    async def async_append_serial_log(
        self,
        controller_id: str,
        *,
        direction: str,
        payload: Any,
    ) -> None:
        """Record a serial RX/TX entry for diagnostics."""

        controller = self._data["controllers"].get(controller_id)
        metadata = dict(controller.get("metadata", {})) if controller is not None else {}
        log = list(metadata.get("serial_log", []))

        entry = {
            "timestamp": int(time.time() * 1000),
            "direction": direction,
            "payload": deepcopy(payload),
        }
        log.append(entry)
        if len(log) > 200:
            log = log[-200:]

        metadata["serial_log"] = log
        if controller is not None:
            controller["metadata"] = metadata
        await self.async_commit()

    async def async_record_learned_switch(
        self,
        *,
        controller_id: str | None,
        switch: int,
        mask: int,
    ) -> None:
        """Track an observed switch press for learn mode."""
        if mask <= 0:
            return

        key = self._make_learned_switch_key(switch, mask)
        existing = self._data["learned_buttons"].get(key)
        timestamp = int(time.time() * 1000)
        if existing is None:
            self._data["learned_buttons"][key] = {
                "id": key,
                "controller_id": controller_id,
                "switch": switch,
                "mask": mask,
                "count": 1,
                "first_seen": timestamp,
                "last_seen": timestamp,
            }
        else:
            existing["last_seen"] = timestamp
            existing["count"] = int(existing.get("count", 0)) + 1
            if controller_id:
                existing["controller_id"] = controller_id

        await self.async_commit()

    async def async_delete_learned_button(self, key: str) -> None:
        """Remove a learned button entry so it can be discovered again."""
        if key in self._data["learned_buttons"]:
            self._data["learned_buttons"].pop(key, None)
            await self.async_commit()

    def _remove_learned_switch_entry(self, switch: Any, mask: Any) -> None:
        key = self._make_learned_switch_key(switch, mask)
        if key in self._data["learned_buttons"]:
            self._data["learned_buttons"].pop(key, None)

    @staticmethod
    def _make_learned_switch_key(switch: Any, mask: Any) -> str:
        try:
            switch_val = int(switch)
        except (TypeError, ValueError):
            switch_val = switch
        try:
            mask_val = int(mask)
        except (TypeError, ValueError):
            mask_val = mask
        return f"{switch_val}:{mask_val}"

    async def async_delete_controller(self, controller_id: str) -> None:
        if controller_id not in self._data["controllers"]:
            return

        # Remove drivers mapped to this controller (and associated outputs)
        for driver_id, driver in list(self._data["drivers"].items()):
            if driver.get("controller_id") == controller_id:
                await self.async_delete_driver(driver_id)

        self._data["controllers"].pop(controller_id, None)
        await self.async_commit()

    # Drivers ---------------------------------------------------------------------

    def get_drivers(self) -> list[dict[str, Any]]:
        return list(self._data["drivers"].values())

    def iter_driver_outputs(self):
        for driver in self._data["drivers"].values():
            for output in driver.get("outputs", []):
                yield driver, output

    def get_output_entry(self, output_id: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        for driver, output in self.iter_driver_outputs():
            if output["id"] == output_id:
                return (driver, output)
        return None

    def list_output_descriptors(self) -> list[dict[str, Any]]:
        descriptors: list[dict[str, Any]] = []
        for driver, output in self.iter_driver_outputs():
            descriptors.append(
                {
                    "id": output["id"],
                    "slot": output["slot"],
                    "name": output.get("name"),
                    "channels": list(output.get("channels", [])),
                    "faulty": output.get("faulty", False),
                    "disabled": output.get("disabled", False),
                    "controller_id": driver.get("controller_id"),
                    "driver_id": driver.get("id"),
                    "driver_index": driver.get("driver_index", 0),
                    "driver_name": driver.get("name"),
                    "min_pwm": output.get("min_pwm", 0),
                    "max_pwm": output.get("max_pwm", 255),
                    "pwm": output.get("pwm", 0),
                    "target_pwm": output.get("target_pwm", output.get("pwm", 0)),
                    "level": output.get("level", 0),
                }
            )
        return descriptors

    async def async_apply_output_targets(self, targets: dict[str, Any]) -> dict[str, int]:
        """Store PWM targets for individual outputs."""

        if not isinstance(targets, dict):
            return {}

        updated: dict[str, int] = {}
        changed = False

        for driver in self._data["drivers"].values():
            outputs = driver.get("outputs", [])
            for output in outputs:
                output_id = output.get("id")
                if not output_id or output_id not in targets:
                    continue
                try:
                    requested = int(targets[output_id])
                except (TypeError, ValueError):
                    continue

                min_pwm = int(output.get("min_pwm", 0))
                max_pwm = int(output.get("max_pwm", 255))
                if max_pwm < min_pwm:
                    max_pwm = min_pwm

                if output.get("disabled"):
                    requested = min_pwm
                else:
                    requested = max(min_pwm, min(max_pwm, requested))

                previous = output.get("target_pwm")
                if previous is not None and int(previous) == requested:
                    continue

                output["target_pwm"] = requested
                updated[output_id] = requested
                changed = True

        if changed:
            await self.async_commit()

        return updated

    def resolve_output_ids(self, output_ids: Iterable[str]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        resolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for output_id in output_ids or []:
            entry = self.get_output_entry(output_id)
            if entry is not None:
                resolved.append(entry)
        return resolved

    async def async_upsert_driver(self, driver: dict[str, Any]) -> dict[str, Any]:
        driver_id = driver.get("id") or _new_id("drv")
        stored = self._data["drivers"].get(
            driver_id,
            {
                "id": driver_id,
                "name": driver.get("name") or driver_id,
                "controller_id": driver.get("controller_id"),
                "driver_index": int(driver.get("driver_index", 0)),
                "outputs": [],
                "metadata": _normalize_metadata(driver.get("metadata", {})),
            },
        )

        stored.update(
            {
                "name": driver.get("name", stored.get("name", driver_id)),
                "controller_id": driver.get("controller_id", stored.get("controller_id")),
                "driver_index": int(driver.get("driver_index", stored.get("driver_index", 0))),
                "metadata": _normalize_metadata(driver.get("metadata", stored.get("metadata", {}))),
            }
        )

        stored["outputs"] = self._normalize_driver_outputs(
            driver_id,
            driver.get("outputs", []),
            stored.get("outputs", []),
        )

        self._data["drivers"][driver_id] = stored
        await self.async_commit()
        return stored

    async def async_delete_driver(self, driver_id: str) -> None:
        if driver_id not in self._data["drivers"]:
            return

        driver = self._data["drivers"].pop(driver_id)
        output_ids = [output["id"] for output in driver.get("outputs", [])]

        for group in self._data["groups"].values():
            group_leds = group.get("led_ids", [])
            if not group_leds:
                continue
            group["led_ids"] = [led_id for led_id in group_leds if led_id not in output_ids]

        await self.async_commit()

    def _normalize_driver_outputs(
        self,
        driver_id: str,
        incoming_outputs: Iterable[dict[str, Any]],
        existing_outputs: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        existing_by_slot = {int(output.get("slot", idx)): output for idx, output in enumerate(existing_outputs)}
        incoming_by_slot = {int(output.get("slot", idx)): output for idx, output in enumerate(incoming_outputs)}

        normalized: list[dict[str, Any]] = []
        for slot in range(TOTAL_OUTPUTS_PER_DRIVER):
            incoming = incoming_by_slot.get(slot, {})
            previous = existing_by_slot.get(slot, {})
            output_id = incoming.get("id") or previous.get("id") or f"{driver_id}_slot{slot}"

            disabled = bool(incoming.get("disabled", previous.get("disabled", False)))
            min_pwm = int(incoming.get("min_pwm", previous.get("min_pwm", 0)))
            max_pwm = int(incoming.get("max_pwm", previous.get("max_pwm", 255)))
            level = int(incoming.get("level", previous.get("level", 0)))
            pwm = int(incoming.get("pwm", previous.get("pwm", 0)))
            try:
                target_pwm = int(incoming.get("target_pwm", previous.get("target_pwm", pwm)))
            except (TypeError, ValueError):
                target_pwm = pwm
            if disabled:
                level = 0
                pwm = min_pwm
                target_pwm = min_pwm
            faulty = False if disabled else bool(incoming.get("faulty", previous.get("faulty", False)))

            normalized.append(
                {
                    "id": output_id,
                    "slot": slot,
                    "name": incoming.get("name", previous.get("name", f"LED {slot + 1}")),
                    "channels": _normalize_channels(incoming.get("channels", previous.get("channels", [slot]))),
                    "disabled": disabled,
                    "faulty": faulty,
                    "pwm": pwm,
                    "level": level,
                    "min_pwm": min_pwm,
                    "max_pwm": max_pwm,
                    "target_pwm": target_pwm,
                }
            )

        return normalized

    # Groups ----------------------------------------------------------------------

    def get_groups(self) -> list[dict[str, Any]]:
        return list(self._data["groups"].values())

    def build_group_channel_map(
        self,
        group_id: str,
        *,
        include_disabled: bool = False,
        include_faulty: bool = False,
    ) -> dict[str, dict[int, list[int]]]:
        """Return controller/driver/channel mappings for the given group."""

        group = self.get_group(group_id)
        if group is None:
            return {}

        mapping: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))

        for driver, output in self.resolve_output_ids(group.get("led_ids", [])):
            if not include_disabled and output.get("disabled"):
                continue
            if not include_faulty and output.get("faulty"):
                continue

            controller_id = driver.get("controller_id")
            if controller_id is None:
                continue

            channels = list(output.get("channels") or [])
            if not channels:
                slot = output.get("slot")
                if slot is not None:
                    channels = [slot]

            if not channels:
                continue

            try:
                driver_index = int(driver.get("driver_index", 0))
            except (TypeError, ValueError):
                continue

            for channel in channels:
                try:
                    channel_index = int(channel)
                except (TypeError, ValueError):
                    continue
                mapping[controller_id][driver_index].add(channel_index)

        result: dict[str, dict[int, list[int]]] = {}
        for controller_id, driver_map in mapping.items():
            result[controller_id] = {}
            for driver_index, channel_set in driver_map.items():
                result[controller_id][driver_index] = sorted(channel_set)

        return result

    def build_group_pwm_map(
        self,
        group_id: str,
        *,
        include_disabled: bool = False,
        include_faulty: bool = False,
        brightness: int | None = None,
    ) -> dict[str, dict[int, list[int]]]:
        """Return controller/driver/slot PWM arrays for the given group.

        Slots not addressed by the group are set to -1 so the device can ignore them.
        """

        group = self.get_group(group_id)
        if group is None:
            return {}

        try:
            brightness_value = int(brightness if brightness is not None else group.get("brightness", 0))
        except (TypeError, ValueError):
            brightness_value = 0
        brightness_value = max(0, min(100, brightness_value))

        mapping: dict[str, dict[int, list[int]]] = defaultdict(dict)

        for driver, output in self.resolve_output_ids(group.get("led_ids", [])):
            if not include_disabled and output.get("disabled"):
                continue
            if not include_faulty and output.get("faulty"):
                continue

            controller_id = driver.get("controller_id")
            if controller_id is None:
                continue

            channels = list(output.get("channels") or [])
            if not channels:
                slot = output.get("slot")
                if slot is not None:
                    channels = [slot]
            if not channels:
                continue

            try:
                driver_index = int(driver.get("driver_index", 0))
            except (TypeError, ValueError):
                continue

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

            pwm_value = int(min_pwm + (max_pwm - min_pwm) * (brightness_value / 100.0))

            slots = mapping[controller_id].setdefault(driver_index, [-1, -1, -1, -1])
            for channel in channels:
                try:
                    channel_index = int(channel)
                except (TypeError, ValueError):
                    continue
                if 0 <= channel_index < len(slots):
                    slots[channel_index] = pwm_value

        return mapping

    def get_group(self, group_id: str) -> dict[str, Any] | None:
        return self._data["groups"].get(group_id)

    async def async_upsert_group(self, group: dict[str, Any]) -> dict[str, Any]:
        group_id = group.get("id") or _new_id("group")
        stored = self._data["groups"].get(
            group_id,
            {
                "id": group_id,
                "name": group.get("name") or group_id,
                "led_ids": _normalize_output_ids(group.get("led_ids", [])),
                "is_on": bool(group.get("is_on", False)),
                "brightness": int(group.get("brightness", 0)),
            },
        )

        stored.update(
            {
                "name": group.get("name", stored["name"]),
                "led_ids": _normalize_output_ids(group.get("led_ids", stored.get("led_ids", []))),
                "is_on": bool(group.get("is_on", stored.get("is_on", False))),
                "brightness": int(group.get("brightness", stored.get("brightness", 0))),
            }
        )

        self._data["groups"][group_id] = stored
        await self.async_commit()
        return stored

    async def async_delete_group(self, group_id: str) -> None:
        if group_id in self._data["groups"]:
            self._data["groups"].pop(group_id, None)
            await self.async_commit()

    # Derived views ----------------------------------------------------------------

    def groups_state_view(self) -> dict[str, dict[str, Any]]:
        """Return group state data exposed to Home Assistant entities."""
        output_lookup = {
            output["id"]: (driver, output)
            for driver, output in self.iter_driver_outputs()
        }

        result: dict[str, dict[str, Any]] = {}
        for group in self._data["groups"].values():
            outputs = []
            faulty = []
            for led_id in group.get("led_ids", []):
                entry = output_lookup.get(led_id)
                if entry is None:
                    continue
                _, output = entry
                if output.get("disabled"):
                    continue
                outputs.append(output)
                if output.get("faulty"):
                    faulty.append(output["id"])

            result[group["id"]] = {
                "name": group.get("name"),
                "is_on": group.get("is_on", False),
                "brightness": group.get("brightness"),
                "led_ids": [output["id"] for output in outputs],
                "led_count": len(outputs),
                "faulty_leds": faulty,
            }

        return result

    # SSR ------------------------------------------------------------------------

    def get_ssr_config(self) -> dict[str, Any]:
        ssr = self._data.setdefault("ssr", {"base_address": 0, "entries": {}})
        if "entries" not in ssr or not isinstance(ssr["entries"], dict):
            ssr["entries"] = {}
        if "base_address" not in ssr:
            ssr["base_address"] = 0
        return ssr

    def get_ssr_base_address(self) -> int:
        return int(self.get_ssr_config().get("base_address", 0))

    async def async_set_ssr_base_address(self, base_address: Any) -> int:
        try:
            value = int(base_address)
        except (TypeError, ValueError):
            raise ValueError("Base address must be an integer") from None
        if value < 0:
            value = 0
        if value > 255:
            value = 255
        config = self.get_ssr_config()
        config["base_address"] = value
        await self.async_commit()
        return value

    def list_ssr_entries(self) -> list[dict[str, Any]]:
        entries = self.get_ssr_config().get("entries", {})
        return [deepcopy(entry) for entry in entries.values()]

    def get_ssr_entry(self, entry_id: str) -> dict[str, Any] | None:
        return deepcopy(self.get_ssr_config().get("entries", {}).get(entry_id))

    async def async_upsert_ssr_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        config = self.get_ssr_config()
        entries = config.setdefault("entries", {})
        entry_id = entry.get("id") or _new_id("ssr")

        if entry_id not in entries and len(entries) >= SSR_MAX_ENTRIES:
            raise ValueError(f"Maximum of {SSR_MAX_ENTRIES} SSR entries reached")

        try:
            bit_index = _validate_ssr_bit(entry.get("bit_index"))
        except ValueError as err:
            raise ValueError(str(err)) from err

        for existing_id, existing in entries.items():
            if existing_id == entry_id:
                continue
            if int(existing.get("bit_index", -1)) == bit_index:
                raise ValueError(f"Bit {bit_index} is already assigned to another SSR")

        stored = entries.get(
            entry_id,
            {
                "id": entry_id,
                "name": entry.get("name") or entry_id,
                "bit_index": bit_index,
                "group_id": entry.get("group_id"),
                "is_on": bool(entry.get("is_on", False)),
            },
        )

        stored.update(
            {
                "name": entry.get("name", stored.get("name", entry_id)),
                "bit_index": bit_index,
                "group_id": entry.get("group_id", stored.get("group_id")),
                "is_on": bool(entry.get("is_on", stored.get("is_on", False))),
            }
        )

        entries[entry_id] = stored
        await self.async_commit()
        return deepcopy(stored)

    async def async_delete_ssr_entry(self, entry_id: str) -> None:
        config = self.get_ssr_config()
        entries = config.setdefault("entries", {})
        if entry_id in entries:
            entries.pop(entry_id, None)
            await self.async_commit()

    def get_ssr_state_mask(self) -> int:
        mask = 0
        for entry in self.get_ssr_config().get("entries", {}).values():
            bit_index = entry.get("bit_index")
            try:
                bit = int(bit_index)
            except (TypeError, ValueError):
                continue
            if 0 <= bit < SSR_MAX_BITS and bool(entry.get("is_on")):
                mask |= (1 << bit)
        return mask

    def set_ssr_entry_state(self, entry_id: str, turn_on: bool) -> int:
        config = self.get_ssr_config()
        entries = config.setdefault("entries", {})
        entry = entries.get(entry_id)
        if entry is None:
            raise ValueError("Unknown SSR entry")
        entry["is_on"] = bool(turn_on)
        return self.get_ssr_state_mask()

    # Patch panel ----------------------------------------------------------------

    def get_patch_panel_config(self) -> dict[str, Any]:
        panel = self._data.setdefault("patch_panel", {"ports": {}})
        if "ports" not in panel or not isinstance(panel["ports"], dict):
            panel["ports"] = {}
        return panel

    def list_patch_panel_ports(self) -> list[dict[str, Any]]:
        config = self.get_patch_panel_config()
        entries = config.get("ports", {})
        result: list[dict[str, Any]] = []
        for port_number in range(1, PATCH_PANEL_PORTS + 1):
            key = str(port_number)
            stored = entries.get(key)
            if stored is None:
                stored = {
                    "id": key,
                    "port_number": port_number,
                    "label": f"Port {port_number}",
                    "notes": "",
                    "led_ids": [],
                }
            result.append(deepcopy(stored))
        return result

    async def async_upsert_patch_panel_port(self, port: dict[str, Any]) -> dict[str, Any]:
        config = self.get_patch_panel_config()
        entries = config.setdefault("ports", {})
        try:
            port_number = int(port.get("port_number"))
        except (TypeError, ValueError):
            raise ValueError("port_number must be an integer") from None
        if port_number < 1 or port_number > PATCH_PANEL_PORTS:
            raise ValueError(f"port_number must be between 1 and {PATCH_PANEL_PORTS}")

        key = str(port_number)
        stored = entries.get(
            key,
            {
                "id": key,
                "port_number": port_number,
                "label": port.get("label") or f"Port {port_number}",
                "notes": port.get("notes", ""),
                "led_ids": _normalize_output_ids(port.get("led_ids", [])),
            },
        )

        stored.update(
            {
                "label": port.get("label", stored.get("label", f"Port {port_number}")),
                "notes": port.get("notes", stored.get("notes", "")),
                "led_ids": _normalize_output_ids(port.get("led_ids", stored.get("led_ids", []))),
            }
        )

        entries[key] = stored
        await self.async_commit()
        return deepcopy(stored)


def serialize_registry_snapshot(registry: LedRegistry) -> dict[str, Any]:
    """Return a safe snapshot of registry contents for the API."""

    return {
        "controllers": deepcopy(registry.get_controllers()),
        "drivers": deepcopy(registry.get_drivers()),
        "groups": deepcopy(registry.get_groups()),
        "led_outputs": registry.list_output_descriptors(),
        "switches": deepcopy(registry.get_switches()),
        "buttons": deepcopy(registry.get_buttons()),
        "learned_buttons": deepcopy(registry.get_learned_buttons()),
        "ssr": {
            "base_address": registry.get_ssr_base_address(),
            "entries": registry.list_ssr_entries(),
        },
        "patch_panel": {
            "ports": registry.list_patch_panel_ports(),
        },
    }
