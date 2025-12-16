"""Switch platform for LED driver groups."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_BRIGHTNESS, ATTR_GROUP_ID, ATTR_LED_IDS, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities from a config entry."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data["coordinator"]
    manager = entry_data["manager"]

    entities: dict[str, LedDriverSwitch] = {}

    @callback
    def _sync_entities() -> None:
        if not coordinator.data:
            return
        new_entities: list[LedDriverSwitch] = []
        for group_id in coordinator.data:
            if group_id not in entities:
                entity = LedDriverSwitch(
                    group_id=group_id,
                    coordinator=coordinator,
                    manager=manager,
                    entry=entry,
                )
                entities[group_id] = entity
                new_entities.append(entity)
        if new_entities:
            async_add_entities(new_entities)

    _sync_entities()
    remove_listener = coordinator.async_add_listener(_sync_entities)
    entry_data.setdefault("_entity_listeners", []).append(remove_listener)


class LedDriverSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of a LED group as a switch entity."""

    def __init__(
        self,
        *,
        group_id: str,
        coordinator: Any,
        manager: Any,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._manager = manager
        self._entry = entry
        self._group_id = group_id
        self._attr_unique_id = f"{entry.entry_id}_{group_id}"

    @property
    def name(self) -> str:
        return self._group_state.get("name") or self._group_id

    @property
    def is_on(self) -> bool:
        """Return the on/off state."""
        group_state = self._group_state
        return bool(group_state.get("is_on"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose raw attributes."""
        group_state = self._group_state
        return {
            ATTR_GROUP_ID: self._group_id,
            ATTR_BRIGHTNESS: group_state.get("brightness"),
            ATTR_LED_IDS: group_state.get("led_ids"),
            "faulty_leds": group_state.get("faulty_leds"),
            "led_count": group_state.get("led_count"),
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Describe the parent device."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name="LED Driver",
            manufacturer="Example",
            model="JSON LED Driver",
        )

    @property
    def _group_state(self) -> dict[str, Any]:
        return self.coordinator.data.get(self._group_id, {})

    @property
    def available(self) -> bool:
        return self._group_id in self.coordinator.data

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the LED group on."""
        await self._manager.async_apply_group_action(self._group_id, "on")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the LED group off."""
        await self._manager.async_apply_group_action(self._group_id, "off")

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
