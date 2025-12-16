"""Common constants for the LED Driver integration."""

from homeassistant.const import Platform

DOMAIN = "s2j_led_driver"
PLATFORMS: list[Platform] = [Platform.SENSOR]

DEFAULT_BAUDRATE = 115200

ATTR_GROUP_ID = "group_id"
ATTR_BRIGHTNESS = "brightness"
ATTR_LED_IDS = "led_ids"
