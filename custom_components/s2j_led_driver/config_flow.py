"""Config flow for the LED driver integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_NAME

from .const import DOMAIN

SINGLETON_ID = "s2j_led_driver_integration"


class LedDriverConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for the LED driver."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Present a minimal configuration form."""
        if user_input is not None:
            await self.async_set_unique_id(SINGLETON_ID)
            self._abort_if_unique_id_configured()
            title = user_input[CONF_NAME]
            return self.async_create_entry(title=title, data={CONF_NAME: title})

        schema = vol.Schema({vol.Required(CONF_NAME, default="LED Driver"): str})
        return self.async_show_form(step_id="user", data_schema=schema)
