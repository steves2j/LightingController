# LED Driver Controller (Home Assistant custom integration)

This integration manages LED driver controllers over serial/CAN, tracks LEDs and groups, and exposes them as Home Assistant entities. A bundled panel (`/local/led_driver_panel/index.html`) provides rich management (controllers, LEDs, groups, switches, SSR, diagnostics).

## Installation

### Manual
1. Copy `custom_components/s2j_led_driver` into your HA `/config/custom_components/` folder.
2. (HACS installs the panel assets automatically under the integration; no manual copy to `/www` needed.)
3. Restart Home Assistant.
4. In Settings → Devices & Services → Add Integration, search for “LED Driver Controller” and follow the flow.

### HACS (recommended)
- If you don't have HACS installed already you will need to add that prior to using this method of installing the "LED Driver Controller". To do that follow these steps.
This requires a github account. If you don't have one already. Create one by visiting github.com.

1. Go to your Home Assistant webpage.
2. Click on Settings.
3. Click Add-ons (If you don't have this then you are running HA in windows or MacOS or linux and should follow the Manual install).
4. Click Add-on store.
5. There should be three dots on the top right : click that and select Repositories.
6. Add https://github.com/hacs/addons, click Add, wait and then click close.
7. Refresh your browser. CTRL-F5 on windows or CMD-R on Macos.
8. Repeat steps 1-4. Once the Add-on store is loaded you should see a Get HACS button. If you can't see it try searching for Get HACS.
9. Click it. Then Click Install.
10. Repeat steps 1-3. Now click on Get HACS.
11. Enable Start on boot (Slider to right). And click start.
12. Once installed. Restart HA. (Settings->System->Power button top right-> Restart Home Assistant).
13. Click on Settings.
14. Click on Devices & Services.
15. Click Add integration.
16. Search for HACS. Click on it.
17. Confirm all the check boxes. And click Submit.
18. Follow the onscreen Github instructions.

- After install, copy the `www/led_driver_panel` folder (and `www/controller_overview.html`) into your HA `/config/www/` so the sidebar panel (`/local/led_driver_panel/index.html`) works. HACS installs the integration under `custom_components`, but does not place assets into `/www` automatically.

- Add this repository as a custom HACS integration (until listed), then install “LED Driver Controller”. Restart HA and add the integration. To do this follow these instructions.
1. Go to your Home Assistant webpage.
2. Click on HACS (If you don't see the follow the above steps to install HACS).
3. Click on the triple dots : Select Custom Repositories.
4. Add https://github.com/steves2j/LightingController as a repository and Integration as the type.
5. Click Add.
6. You should see LED Driver Controller appear about the Repository entry box with a Red BIN. Click X to close this dialog.
7. Type S2J in the search list. Click on the returned search list.
8. Click on Download. And Click on Download in dialog.
9. Once downloaded click on Settings and acknowledge the Restart required, or Go to system->Power Button->Restart.
10. Once restarted, Click on settings->Devices & Services->Add integration.
11. Seach for S2J and click on LED Driver Controller.
12. Add a new integration name. You can call it whatever you like or leave it as LED Driver.
13. Add and Finish. Add any Floor or Area as you seem fit. Or skip it.
14. And your done. You should see LED Driver on the HA list.

## Configuration
- The config flow creates a single instance (one entry). Serial ports and drivers are managed through the panel.
- The panel is served from `/s2j_led_driver_static/led_driver_panel/index.html`; the controller overview is at `/s2j_led_driver_static/controller_overview.html`.
If you want the controller_overview to always display in the Overview, Edit your Dashboard and update to the following. To be able to do this you will need to "Tale Control" when you click on the edit icon (NOTE: Taking control will expose your inner access and can be used to circumvent security if your HA is exposed to the public).
views:
  - path: default_view
    title: Home
    type: panel
    cards:
      - type: iframe
        url: /s2j_led_driver_static/controller_overview.html
        aspect_ratio: 75%

## Development
- Key code: `custom_components/s2j_led_driver` (logic, registry, API, entities), `www/led_driver_panel` + `www/controller_overview.html` (frontend).
- Requirements: `pyserial`, `pyserial-asyncio`.

## USB firmware updates

The controller's **Restore Settings** action restores a backup independently
of firmware updates. Load a JSON file or paste the complete backup into the
dialog, then choose **Upload and restore** and confirm. Raw `version`/`settings`
backups and `cm: settings, a: restore, data: ...` command envelopes are accepted.
This uses the main serial connection inside HA, including on HAOS; no external
terminal is needed. Polling pauses during restore and resumes afterward.
Success requires a restart and matching settings readback. It replaces the
whole device configuration and may enable saved LED outputs. Administrator
access and firmware supporting settings restore are required.

Each saved controller has an **Upd FW** action. Select the PlatformIO-generated
`firmware.zip` (not `firmware.bin`); the integration backs up device settings,
releases the controller serial port, enters the XIAO bootloader with the
1200-baud touch, transfers the DFU package, reconnects, and compares the
post-update settings. If they differ, the dialog provides both snapshots and a
button to restore the saved device settings.

The backup is saved in HA storage before entering DFU and can be downloaded
from the dialog. Reopening **Upd FW** resumes progress or shows the last
result. Normal commands, status polling, and terminal access are blocked
during maintenance. Restore restarts the controller and reads the settings
back before declaring success; differing values are never silently restored.
Only application-only ZIP packages linked for this controller are accepted.
Packages have CRC integrity checks, not cryptographic signature verification:
upload only firmware you trust. Firmware upload requires an HA administrator.

If settings backup fails for any reason (including a timeout or a controller
already in DFU mode), the dialog
offers **Continue without backup**. This requires explicit confirmation and
retries the selected ZIP without a pre-update snapshot. Settings cannot be
compared or automatically restored in this mode. After reopening the dialog,
you may need to select the ZIP again. Failures outside the backup stage do not
offer this override. The updater attempts DFU directly when a stable single
USB interface is present; it only requests a reset for the dual-interface
application. USB enumeration is polled for up to 30 seconds per transition.
Retries retain the selected device identity even if its port name changes.

The HA host/container must have access to the XIAO USB device. The integration
implements the XIAO's USB serial DFU framing itself, using only its existing
`pyserial` dependency—no `nrfutil`, `adafruit-nrfutil`, or external updater is
required. The controller firmware must include the `settings backup` and
`settings restore` HA JSON support introduced alongside this feature.

Local tests (from the HA project directory):

```sh
.venv/bin/python tools/test_native_dfu.py
# Starts the existing config/ HA instance and flashes the attached TEST board.
# Also changes/restores its display name to verify settings recovery.
.venv/bin/python tools/test_firmware_hardware.py --flash --restore-test --port /dev/cu.usbmodem14101
```

The hardware harness leaves HA running on http://localhost:8123 after testing.

## Issues / Docs
- Docs & issues: https://github.com/steves2j/LightingController/issues
