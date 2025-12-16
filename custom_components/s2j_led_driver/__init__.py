"""Home Assistant integration scaffolding for the LED driver."""

from __future__ import annotations

from collections import defaultdict
import logging
from typing import Any

from homeassistant.components import frontend
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import async_register_http_views
from .const import DOMAIN, PLATFORMS
from .manager import LedDriverManager
from .patches import ensure_safe_tcp_keepalive
from .registry import LedRegistry

_LOGGER = logging.getLogger(__name__)

type LedDriverConfigEntry = ConfigEntry


async def async_setup(hass: HomeAssistant, _config: ConfigType) -> bool:
    """Set up the integration from YAML."""
    hass.data.setdefault(DOMAIN, {})
    ensure_safe_tcp_keepalive()
    await _async_get_registry(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: LedDriverConfigEntry) -> bool:
    """Set up LED driver from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})

    registry = await _async_get_registry(hass)
    manager = LedDriverManager(hass, registry)
    await manager.async_initialize()

    coordinator = LedDriverCoordinator(hass, registry)
    metrics_coordinator = LedDriverMetricsCoordinator(hass, registry)
    await coordinator.async_config_entry_first_refresh()
    await metrics_coordinator.async_config_entry_first_refresh()

    domain_data[entry.entry_id] = {
        "manager": manager,
        "coordinator": coordinator,
        "metrics_coordinator": metrics_coordinator,
        "registry": registry,
    }

    if not domain_data.get("_http_registered"):
        await async_register_http_views(hass)
        domain_data["_http_registered"] = True

    if not domain_data.get("_panel_registered"):
        _register_panel(hass)
        domain_data["_panel_registered"] = True

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: LedDriverConfigEntry) -> bool:
    """Handle unloading an entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok and (entry_data := hass.data[DOMAIN].pop(entry.entry_id, None)):
        for unsub in entry_data.get("_entity_listeners", []):
            try:
                unsub()
            except Exception:  # pragma: no cover - safeguard
                _LOGGER.debug("Error unsubscribing entity listener", exc_info=True)
        await entry_data["manager"].async_shutdown()
        entry_data["coordinator"].async_shutdown()
        entry_data["metrics_coordinator"].async_shutdown()

    remaining = [key for key in hass.data[DOMAIN] if key not in {"registry", "_http_registered", "_panel_registered"}]
    if not remaining:
        _remove_panel(hass)
        hass.data[DOMAIN].pop("_panel_registered", None)

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: LedDriverConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_get_registry(hass: HomeAssistant) -> LedRegistry:
    domain_data = hass.data.setdefault(DOMAIN, {})
    registry: LedRegistry | None = domain_data.get("registry")
    if registry is None:
        registry = LedRegistry(hass)
        await registry.async_load()
        domain_data["registry"] = registry
    return registry


class LedDriverCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Expose registry group state to Home Assistant entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        registry: LedRegistry,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="s2j_led_driver",
        )
        self._registry = registry
        self._listener = self._handle_registry_update
        registry.async_add_listener(self._listener)

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Return the latest registry snapshot."""
        return self._registry.groups_state_view()

    @callback
    def _handle_registry_update(self) -> None:
        self.async_set_updated_data(self._registry.groups_state_view())

    def async_shutdown(self) -> None:
        self._registry.async_remove_listener(self._listener)


class LedDriverMetricsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Aggregate controller metrics for Home Assistant entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        registry: LedRegistry,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="s2j_led_driver_metrics",
        )
        self._registry = registry
        self._listener = self._handle_registry_update
        registry.async_add_listener(self._listener)

    async def _async_update_data(self) -> dict[str, Any]:
        return build_metrics_snapshot(self._registry)

    @callback
    def _handle_registry_update(self) -> None:
        self.async_set_updated_data(build_metrics_snapshot(self._registry))

    def async_shutdown(self) -> None:
        self._registry.async_remove_listener(self._listener)


def _register_panel(hass: HomeAssistant) -> None:
    frontend.async_register_built_in_panel(
        hass,
        component_name="iframe",
        sidebar_title="LED Driver",
        sidebar_icon="mdi:led-strip-variant",
        frontend_url_path="led-driver",
        config={"url": "/local/led_driver_panel/index.html"},
    )


def _remove_panel(hass: HomeAssistant) -> None:
    frontend.async_remove_panel(hass, "led-driver")


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def build_metrics_snapshot(registry: LedRegistry) -> dict[str, Any]:
    """Return aggregate power metrics derived from controller metadata."""

    totals = {
        "power": 0.0,
        "current": 0.0,
        "voltage": 0.0,
    }
    voltage_sum = 0.0
    voltage_count = 0
    sensors: list[dict[str, Any]] = []
    controller_summaries: list[dict[str, Any]] = []

    drivers_by_controller: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for driver in registry.get_drivers():
        controller_id = driver.get("controller_id")
        if controller_id:
            drivers_by_controller[controller_id].append(driver)

    for controller in registry.get_controllers():
        metadata = controller.get("metadata") or {}
        status = metadata.get("status") or {}

        totals["power"] += _safe_float(status.get("total_power"))
        totals["current"] += _safe_float(status.get("total_current"))
        controller_voltage = status.get("total_voltage")
        controller_samples = status.get("voltage_sample_count") or 0
        if controller_voltage is not None and controller_samples:
            voltage_sum += _safe_float(controller_voltage) * controller_samples
            voltage_count += controller_samples

        led_total = 0
        led_on = 0
        led_fault = 0
        for driver in drivers_by_controller.get(controller.get("id"), []):
            for output in driver.get("outputs", []) or []:
                if output.get("disabled"):
                    continue
                led_total += 1
                if int(output.get("level", 0)) > 0:
                    led_on += 1
                if output.get("faulty"):
                    led_fault += 1

        controller_summaries.append(
            {
                "id": controller.get("id"),
                "name": controller.get("name") or controller.get("id"),
                "power": _safe_float(status.get("total_power")),
                "current": _safe_float(status.get("total_current")),
                "voltage": controller_voltage,
                "voltage_sample_count": controller_samples,
                "led_total": led_total,
                "led_on": led_on,
                "led_fault": led_fault,
            }
        )

        for sensor in status.get("acs", []) or []:
            sensors.append(
                {
                    "controller_id": controller.get("id"),
                    "controller_name": controller.get("name") or controller.get("id"),
                    "bus": sensor.get("bus"),
                    "index": sensor.get("index"),
                    "ready": sensor.get("ready"),
                    "valid": sensor.get("valid"),
                    "power": _safe_float(sensor.get("power")),
                    "current": _safe_float(sensor.get("current")),
                    "voltage": _safe_float(sensor.get("voltage")),
                }
            )

    if voltage_count:
        totals["voltage"] = voltage_sum / voltage_count
    else:
        totals["voltage"] = 0.0

    return {
        "totals": totals,
        "controllers": controller_summaries,
        "sensors": sensors,
    }
