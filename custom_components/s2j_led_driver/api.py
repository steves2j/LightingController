"""HTTP endpoints for the LED driver panel."""

from __future__ import annotations   

from typing import Any
import logging

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .registry import serialize_registry_snapshot
from .manager import LedDriverError

_LOGGER = logging.getLogger(__name__)


async def async_register_http_views(hass: HomeAssistant) -> None:
    """Register HTTP views for the integration."""
    hass.http.register_view(LedDriverStateView(hass))
    hass.http.register_view(LedDriverCommandView(hass))
    hass.http.register_view(LedDriverOutputTargetsView(hass))
    hass.http.register_view(LedDriverEntriesView(hass))
    hass.http.register_view(LedDriverRegistryView(hass))
    hass.http.register_view(LedDriverRegistryControllersView(hass))
    hass.http.register_view(LedDriverRegistryControllersDeleteView(hass))
    hass.http.register_view(LedDriverRegistryDriversView(hass))
    hass.http.register_view(LedDriverRegistryDriversDeleteView(hass))
    hass.http.register_view(LedDriverRegistryGroupsView(hass))
    hass.http.register_view(LedDriverRegistryGroupsDeleteView(hass))
    hass.http.register_view(LedDriverRegistrySwitchesView(hass))
    hass.http.register_view(LedDriverRegistrySwitchesDeleteView(hass))
    hass.http.register_view(LedDriverRegistryButtonsView(hass))
    hass.http.register_view(LedDriverRegistryButtonsDeleteView(hass))
    hass.http.register_view(LedDriverRegistryLearnedButtonsDeleteView(hass))
    hass.http.register_view(LedDriverRegistrySsrBaseView(hass))
    hass.http.register_view(LedDriverRegistrySsrEntriesView(hass))
    hass.http.register_view(LedDriverRegistrySsrEntriesDeleteView(hass))
    hass.http.register_view(LedDriverRegistryPatchPanelPortsView(hass))


class LedDriverBaseView(HomeAssistantView):
    """Shared helpers for LED driver HTTP views."""

    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def _resolve_entry(self, entry_id: str) -> dict[str, Any]:
        """Return the integration data for an entry id."""
        domain_data = self.hass.data.get(DOMAIN, {})
        entry_data = domain_data.get(entry_id)
        if entry_data is None:
            raise web.HTTPNotFound(text="Invalid entry id")
        return entry_data


class LedDriverStateView(LedDriverBaseView):
    """Expose current state to the custom panel."""

    url = "/api/s2j_led_driver/{entry_id}/state"
    name = "api:s2j_led_driver:state"

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        entry_data = self._resolve_entry(entry_id)
        coordinator = entry_data["coordinator"]
        return web.json_response(coordinator.data)


class LedDriverCommandView(LedDriverBaseView):
    """Allow the custom panel to send commands."""

    url = "/api/s2j_led_driver/{entry_id}/command"
    name = "api:s2j_led_driver:command"

    async def post(self, request: web.Request, entry_id: str) -> web.Response:
        payload = await request.json()
        command = payload.get("command")

        if command == "set_group":
            entry_data = self._resolve_entry(entry_id)
            manager = entry_data["manager"]
            group_id = payload.get("group_id")
            action = (payload.get("action") or ("on" if payload.get("on") else "off")).lower()
            if not group_id:
                raise web.HTTPBadRequest(text="Missing group_id")
            if action not in {"on", "off"}:
                raise web.HTTPBadRequest(text="Unsupported action")
            _LOGGER.debug("API set_group entry=%s group=%s action=%s payload=%s", entry_id, group_id, action, payload)
            try:
                response = await manager.async_apply_group_action(group_id, action)
            except LedDriverError as err:
                raise web.HTTPBadRequest(text=str(err)) from err
            return web.json_response({"status": "ok", "responses": response})

        if command == "set_controller_poll":
            entry_data = self._resolve_entry(entry_id)
            manager = entry_data["manager"]
            controller_id = payload.get("controller_id")
            if not controller_id:
                raise web.HTTPBadRequest(text="Missing controller_id")
            enabled_val = payload.get("enabled", False)
            if isinstance(enabled_val, str):
                enabled = enabled_val.strip().lower() in {"1", "true", "yes", "on"}
            else:
                enabled = bool(enabled_val)
            await manager.async_set_controller_poll(controller_id, enabled)
            return web.json_response({"status": "ok"})

        if command == "set_led_config":
            entry_data = self._resolve_entry(entry_id)
            manager = entry_data["manager"]
            configs = payload.get("configs")
            if not isinstance(configs, list):
                raise web.HTTPBadRequest(text="Payload must include 'configs' list")
            try:
                responses = await manager.async_apply_led_configs(configs)
            except LedDriverError as err:
                raise web.HTTPBadRequest(text=str(err)) from err
            return web.json_response({"status": "ok", "responses": responses})

        if command == "set_group_pwm":
            entry_data = self._resolve_entry(entry_id)
            manager = entry_data["manager"]
            group_id = payload.get("group_id")
            if not group_id:
                raise web.HTTPBadRequest(text="Missing group_id")
            targets = payload.get("targets")
            if not isinstance(targets, dict):
                raise web.HTTPBadRequest(text="Missing PWM targets")
            brightness = payload.get("brightness")
            _LOGGER.debug(
                "API set_group_pwm entry=%s group=%s targets=%s brightness=%s",
                entry_id,
                group_id,
                targets,
                brightness,
            )
            try:
                responses = await manager.async_apply_group_pwm_targets(group_id, targets, brightness)
            except LedDriverError as err:
                raise web.HTTPBadRequest(text=str(err)) from err
            return web.json_response({"status": "ok", "responses": responses})

        if command == "set_output_pwm":
            entry_data = self._resolve_entry(entry_id)
            manager = entry_data["manager"]
            output_id = payload.get("output_id")
            pwm = payload.get("pwm")
            if not output_id:
                raise web.HTTPBadRequest(text="Missing output_id")
            _LOGGER.debug("API set_output_pwm entry=%s output=%s pwm=%s", entry_id, output_id, pwm)
            try:
                responses = await manager.async_set_output_pwm(output_id, pwm)
            except LedDriverError as err:
                raise web.HTTPBadRequest(text=str(err)) from err
            return web.json_response({"status": "ok", "responses": responses})

        if command == "set_output_state":
            entry_data = self._resolve_entry(entry_id)
            manager = entry_data["manager"]
            output_id = payload.get("output_id")
            turn_on = payload.get("on", payload.get("turn_on"))
            if not output_id:
                raise web.HTTPBadRequest(text="Missing output_id")
            turn_on_val = bool(turn_on)
            _LOGGER.debug("API set_output_state entry=%s output=%s on=%s", entry_id, output_id, turn_on_val)
            try:
                responses = await manager.async_set_output_state(output_id, turn_on_val)
            except LedDriverError as err:
                raise web.HTTPBadRequest(text=str(err)) from err
            return web.json_response({"status": "ok", "responses": responses})

        if command == "set_ssr_state":
            entry_data = self._resolve_entry(entry_id)
            manager = entry_data["manager"]
            ssr_id = payload.get("ssr_id")
            if not ssr_id:
                raise web.HTTPBadRequest(text="Missing ssr_id")
            on_val = payload.get("on", False)
            if isinstance(on_val, str):
                turn_on = on_val.strip().lower() in {"1", "true", "yes", "on"}
            else:
                turn_on = bool(on_val)
            try:
                response = await manager.async_set_ssr_state(ssr_id, turn_on)
            except LedDriverError as err:
                raise web.HTTPBadRequest(text=str(err)) from err
            return web.json_response({"status": "ok", "response": response})

        raise web.HTTPBadRequest(text="Unsupported command")


class LedDriverOutputTargetsView(LedDriverBaseView):
    """Store per-output PWM targets coming from the UI."""

    url = "/api/s2j_led_driver/{entry_id}/outputs/targets"
    name = "api:s2j_led_driver:outputs:targets"

    async def post(self, request: web.Request, entry_id: str) -> web.Response:
        payload = await request.json()
        targets = payload.get("targets")
        if not isinstance(targets, dict):
            raise web.HTTPBadRequest(text="Payload must include 'targets' map")
        entry_data = self._resolve_entry(entry_id)
        registry = entry_data["registry"]
        updated = await registry.async_apply_output_targets(targets)
        return web.json_response({"updated": updated})


class LedDriverEntriesView(HomeAssistantView):
    """Expose available config entries."""

    url = "/api/s2j_led_driver/entries"
    name = "api:s2j_led_driver:entries"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        entries = [
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
            }
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        ]
        return web.json_response(entries)


class LedDriverRegistryView(LedDriverBaseView):
    """Expose the stored registry."""

    url = "/api/s2j_led_driver/{entry_id}/registry"
    name = "api:s2j_led_driver:registry"

    async def get(self, request: web.Request, entry_id: str) -> web.Response:
        entry_data = self._resolve_entry(entry_id)
        registry = entry_data["registry"]
        return web.json_response(serialize_registry_snapshot(registry))


class _BaseRegistryMutationView(LedDriverBaseView):
    """Base view for registry mutations."""

    section: str

    async def post(self, request: web.Request, entry_id: str) -> web.Response:
        entry_data = self._resolve_entry(entry_id)
        registry = entry_data["registry"]
        payload = await request.json()
        item = payload.get("item")
        if not isinstance(item, dict):
            raise web.HTTPBadRequest(text="Payload must include 'item'")

        if self.section == "controllers":
            stored = await registry.async_upsert_controller(item)
        elif self.section == "drivers":
            stored = await registry.async_upsert_driver(item)
        elif self.section == "groups":
            stored = await registry.async_upsert_group(item)
        elif self.section == "switches":
            stored = await registry.async_upsert_switch(item)
        elif self.section == "buttons":
            stored = await registry.async_upsert_button(item)
        else:
            raise web.HTTPBadRequest(text="Unsupported section")

        return web.json_response(stored)


class _BaseRegistryDeleteView(LedDriverBaseView):
    """Base view for deleting registry entries."""

    section: str

    async def delete(self, request: web.Request, entry_id: str, item_id: str) -> web.Response:
        entry_data = self._resolve_entry(entry_id)
        registry = entry_data["registry"]

        if self.section == "controllers":
            await registry.async_delete_controller(item_id)
        elif self.section == "drivers":
            await registry.async_delete_driver(item_id)
        elif self.section == "groups":
            await registry.async_delete_group(item_id)
        elif self.section == "switches":
            await registry.async_delete_switch(item_id)
        elif self.section == "buttons":
            await registry.async_delete_button(item_id)
        elif self.section == "learned_buttons":
            await registry.async_delete_learned_button(item_id)
        else:
            raise web.HTTPBadRequest(text="Unsupported section")

        return web.json_response({"status": "ok"})


class LedDriverRegistryControllersView(_BaseRegistryMutationView):
    """Create or update controller definitions."""

    url = "/api/s2j_led_driver/{entry_id}/registry/controllers"
    name = "api:s2j_led_driver:registry:controllers"
    section = "controllers"


class LedDriverRegistryControllersDeleteView(_BaseRegistryDeleteView):
    """Delete a controller definition."""

    url = "/api/s2j_led_driver/{entry_id}/registry/controllers/{item_id}"
    name = "api:s2j_led_driver:registry:controllers:delete"
    section = "controllers"


class LedDriverRegistryDriversView(_BaseRegistryMutationView):
    """Create or update driver definitions."""

    url = "/api/s2j_led_driver/{entry_id}/registry/drivers"
    name = "api:s2j_led_driver:registry:drivers"
    section = "drivers"


class LedDriverRegistryDriversDeleteView(_BaseRegistryDeleteView):
    """Delete a driver definition."""

    url = "/api/s2j_led_driver/{entry_id}/registry/drivers/{item_id}"
    name = "api:s2j_led_driver:registry:drivers:delete"
    section = "drivers"


class LedDriverRegistryGroupsView(_BaseRegistryMutationView):
    """Create or update group definitions."""

    url = "/api/s2j_led_driver/{entry_id}/registry/groups"
    name = "api:s2j_led_driver:registry:groups"
    section = "groups"


class LedDriverRegistryGroupsDeleteView(_BaseRegistryDeleteView):
    """Delete a group definition."""

    url = "/api/s2j_led_driver/{entry_id}/registry/groups/{item_id}"
    name = "api:s2j_led_driver:registry:groups:delete"
    section = "groups"


class LedDriverRegistrySwitchesView(_BaseRegistryMutationView):
    """Create or update switch definitions."""

    url = "/api/s2j_led_driver/{entry_id}/registry/switches"
    name = "api:s2j_led_driver:registry:switches"
    section = "switches"


class LedDriverRegistrySwitchesDeleteView(_BaseRegistryDeleteView):
    """Delete a switch definition."""

    url = "/api/s2j_led_driver/{entry_id}/registry/switches/{item_id}"
    name = "api:s2j_led_driver:registry:switches:delete"
    section = "switches"


class LedDriverRegistryButtonsView(_BaseRegistryMutationView):
    """Create or update button definitions."""

    url = "/api/s2j_led_driver/{entry_id}/registry/buttons"
    name = "api:s2j_led_driver:registry:buttons"
    section = "buttons"


class LedDriverRegistryButtonsDeleteView(_BaseRegistryDeleteView):
    """Delete a button definition."""

    url = "/api/s2j_led_driver/{entry_id}/registry/buttons/{item_id}"
    name = "api:s2j_led_driver:registry:buttons:delete"
    section = "buttons"


class LedDriverRegistryLearnedButtonsDeleteView(_BaseRegistryDeleteView):
    """Delete a learned button discovery entry."""

    url = "/api/s2j_led_driver/{entry_id}/registry/learned_buttons/{item_id}"
    name = "api:s2j_led_driver:registry:learned_buttons:delete"
    section = "learned_buttons"


class LedDriverRegistrySsrBaseView(LedDriverBaseView):
    """Update SSR base address."""

    url = "/api/s2j_led_driver/{entry_id}/registry/ssr/base"
    name = "api:s2j_led_driver:registry:ssr:base"

    async def post(self, request: web.Request, entry_id: str) -> web.Response:
        payload = await request.json()
        entry_data = self._resolve_entry(entry_id)
        registry = entry_data["registry"]
        base_address = payload.get("base_address")
        try:
            value = await registry.async_set_ssr_base_address(base_address)
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err)) from err
        return web.json_response({"base_address": value})


class LedDriverRegistrySsrEntriesView(LedDriverBaseView):
    """Create or update SSR entries."""

    url = "/api/s2j_led_driver/{entry_id}/registry/ssr/entries"
    name = "api:s2j_led_driver:registry:ssr:entries"

    async def post(self, request: web.Request, entry_id: str) -> web.Response:
        payload = await request.json()
        item = payload.get("item")
        if not isinstance(item, dict):
            raise web.HTTPBadRequest(text="Payload must include 'item'")
        entry_data = self._resolve_entry(entry_id)
        registry = entry_data["registry"]
        try:
            stored = await registry.async_upsert_ssr_entry(item)
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err)) from err
        return web.json_response(stored)


class LedDriverRegistrySsrEntriesDeleteView(LedDriverBaseView):
    """Delete an SSR entry."""

    url = "/api/s2j_led_driver/{entry_id}/registry/ssr/entries/{item_id}"
    name = "api:s2j_led_driver:registry:ssr:entries:delete"

    async def delete(self, request: web.Request, entry_id: str, item_id: str) -> web.Response:
        entry_data = self._resolve_entry(entry_id)
        registry = entry_data["registry"]
        await registry.async_delete_ssr_entry(item_id)
        return web.json_response({"status": "ok"})


class LedDriverRegistryPatchPanelPortsView(LedDriverBaseView):
    """Create or update patch panel port metadata."""

    url = "/api/s2j_led_driver/{entry_id}/registry/patch_panel/ports"
    name = "api:s2j_led_driver:registry:patch_panel:ports"

    async def post(self, request: web.Request, entry_id: str) -> web.Response:
        payload = await request.json()
        item = payload.get("item")
        if not isinstance(item, dict):
            raise web.HTTPBadRequest(text="Payload must include 'item'")
        entry_data = self._resolve_entry(entry_id)
        registry = entry_data["registry"]
        try:
            stored = await registry.async_upsert_patch_panel_port(item)
        except ValueError as err:
            raise web.HTTPBadRequest(text=str(err)) from err
        return web.json_response(stored)
