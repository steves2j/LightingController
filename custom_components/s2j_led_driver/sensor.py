"""Sensor platform for LED Driver power metrics."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LED driver sensors from a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data["metrics_coordinator"]

    async_add_entities([LedDriverTotalPowerSensor(coordinator, entry)])

    controller_entities: dict[str, LedDriverControllerSensor] = {}

    @callback
    def _sync_controller_sensors() -> None:
        controller_data = (coordinator.data or {}).get("controllers", []) or []
        new_entities: list[LedDriverControllerSensor] = []
        for controller in controller_data:
            controller_id = controller.get("id")
            if not controller_id or controller_id in controller_entities:
                continue
            entity = LedDriverControllerSensor(coordinator, entry, controller_id)
            controller_entities[controller_id] = entity
            new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    _sync_controller_sensors()
    coordinator.async_add_listener(_sync_controller_sensors)

    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    total_id = f"{entry.entry_id}_total_power"
    for entry_id, reg_entry in list(registry.entities.items()):
        unique_id = reg_entry.unique_id
        if (
            unique_id.startswith(prefix)
            and unique_id.endswith("_power")
            and unique_id != total_id
            and reg_entry.config_entry_id == entry.entry_id
        ):
            await registry.async_remove(entry_id)


class LedDriverSensorBase(CoordinatorEntity):
    """Common base for LED driver sensors."""

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="LED Driver",
            manufacturer="Example",
            model="JSON LED Driver",
        )


class LedDriverTotalPowerSensor(LedDriverSensorBase, SensorEntity):
    """Total power usage sensor for all controllers."""

    _attr_name = "LED Driver Total Power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_total_power"

    @property
    def native_value(self) -> float | None:
        totals = (self.coordinator.data or {}).get("totals", {})
        power = totals.get("power")
        return round(float(power),2) if power is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        totals = (self.coordinator.data or {}).get("totals", {})
        return {
            "total_power": totals.get("power"),
            "total_current": totals.get("current"),
            "total_voltage": totals.get("voltage"),
        }

    @property
    def available(self) -> bool:
        totals = (self.coordinator.data or {}).get("totals", {})
        return totals is not None


class LedDriverControllerSensor(LedDriverSensorBase, SensorEntity):
    """Per-controller summary sensor."""

    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    def __init__(self, coordinator, entry: ConfigEntry, controller_id: str) -> None:
        super().__init__(coordinator, entry)
        self._controller_id = controller_id
        self._attr_unique_id = f"{entry.entry_id}_controller_{controller_id}"

    @property
    def name(self) -> str:
        state = self._controller_state
        label = state.get("name") if state else None
        return f"{label or self._controller_id} Power"

    @property
    def native_value(self) -> float | None:
        state = self._controller_state
        if not state:
            return None
        power = state.get("power")
        return round(float(power), 2) if power is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self._controller_state or {}
        return {
            "controller_id": self._controller_id,
            "controller_name": state.get("name"),
            "led_total": state.get("led_total"),
            "led_on": state.get("led_on"),
            "led_fault": state.get("led_fault"),
            "current": state.get("current"),
            "voltage": state.get("voltage"),
        }

    @property
    def available(self) -> bool:
        return self._controller_state is not None

    @property
    def _controller_state(self) -> dict[str, Any] | None:
        controllers = (self.coordinator.data or {}).get("controllers", []) or []
        for controller in controllers:
            if controller.get("id") == self._controller_id:
                return controller
        return None
