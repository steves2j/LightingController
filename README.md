# LED Driver Controller (Home Assistant custom integration)

This integration manages LED driver controllers over serial/CAN, tracks LEDs and groups, and exposes them as Home Assistant entities. A bundled panel (`/local/led_driver_panel/index.html`) provides rich management (controllers, LEDs, groups, switches, SSR, diagnostics).

## Installation

### Manual
1. Copy `custom_components/s2j_led_driver` into your HA `/config/custom_components/` folder.
2. Copy the `www/led_driver_panel` and other `www/*` assets into `/config/www/`.
3. Restart Home Assistant.
4. In Settings → Devices & Services → Add Integration, search for “LED Driver Controller” and follow the flow.

### HACS (recommended)
- Add this repository as a custom HACS integration (until listed), then install “LED Driver Controller”. Restart HA and add the integration.

## Configuration
- The config flow creates a single instance (one entry). Serial ports and drivers are managed through the panel.
- The overview page is served from `/local/led_driver_panel/index.html`; the controller overview is at `/local/controller_overview.html`.

## Development
- Key code: `custom_components/s2j_led_driver` (logic, registry, API, entities), `www/led_driver_panel` + `www/controller_overview.html` (frontend).
- Requirements: `pyserial`, `pyserial-asyncio`.

## Issues / Docs
- Docs & issues: https://github.com/steves2j/LightingController/issues
