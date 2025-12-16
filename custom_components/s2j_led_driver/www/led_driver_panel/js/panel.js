import {
  entrySelect,
  loadButton,
  refreshButton,
  statusEl,
  dashboardContainer,
  groupsContainer,
  switchesContainer,
  buttonDiscoveriesContainer,
  controllersContainer,
  controllerMapContainer,
  driversContainer,
  groupDefsContainer,
  patchPanelVisual,
  patchPanelPortsContainer,
  patchPanelTooltip,
  patchPanelDetails,
  switchVisualContainer,
  switchVisualSelect,
  ssrContainer,
  acsContainer,
  serialLogContainer,
  addControllerButton,
  addDriverButton,
  addGroupButton,
  addSwitchButton,
  addSsrButton,
} from "./dom.js";
import { SWITCH_MASK_CHOICES, AUTO_REFRESH_INTERVAL, SSR_MAX_ENTRIES } from "./constants.js";
import {
  state,
  isEditing,
  addDraft,
  removeDraft,
  syncEditingSet,
  syncEditingSets,
  getItemKey,
  getControllerDisplayName,
  getLedDisplayName,
} from "./state.js";
import {
  formatNumber,
  escapeHtml,
  setError,
  clearError,
  withErrorNotice,
  formatTimestamp,
  clone,
} from "./utils.js";
import {
  apiRequest,
  sendGroupCommand,
  setControllerPolling,
  setControllerCanInterface,
  sendGroupPwmTargets,
  sendLedConfigs,
  setLedOutputTargets,
  setSsrBaseAddress,
  upsertSsrEntry,
  deleteSsrEntry,
  setSsrState,
  upsertPatchPanelPort,
  deleteLearnedButton,
  waitForHass,
} from "./api.js";

const PANEL_VERSION = "4.7"; // increment for visibility per sync request
// Expose version globally for other pages (e.g., controller_overview)
if (typeof window !== "undefined") {
  window.LED_DRIVER_PANEL_VERSION = PANEL_VERSION;
}

const pendingButtonToggles = new Map();
const pendingSsrToggles = new Map();
const PATCH_PANEL_GROUP_RANGES = [
  { start: 1, end: 8, row: 0 },
  { start: 9, end: 16, row: 0 },
  { start: 17, end: 24, row: 0 },
  { start: 25, end: 32, row: 1 },
  { start: 33, end: 40, row: 1 },
  { start: 41, end: 48, row: 1 },
];

function confirmDeletion(message) {
  if (typeof window === "undefined" || typeof window.confirm !== "function") {
    return true;
  }
  return window.confirm(message);
}
const svgPortCache = {
        top: null,
        bottom: null,
        promise: null,
      };

let autoRefreshTimer = null;
let refreshInFlight = false;
let pendingRefreshOptions = null;

      function startAutoRefresh() {
        if (autoRefreshTimer || !AUTO_REFRESH_INTERVAL) {
          return;
        }
        autoRefreshTimer = setInterval(() => {
          if (!state.entryId || refreshInFlight) {
            return;
          }
          if (isEditing()) {
            return;
          }
          refreshAll({ showStatus: false }).catch((error) => {
            console.debug("Auto refresh failed", error);
          });
        }, AUTO_REFRESH_INTERVAL);
      }

      function stopAutoRefresh() {
        if (autoRefreshTimer) {
          clearInterval(autoRefreshTimer);
          autoRefreshTimer = null;
        }
      }
loadButton.addEventListener("click", () => withErrorNotice(() => refreshAll({ showStatus: true, force: true })));
      refreshButton.addEventListener("click", () => withErrorNotice(() => refreshAll({ showStatus: true, force: true })));

      entrySelect.addEventListener("change", () => {
        state.entryId = entrySelect.value || "";
        withErrorNotice(() => refreshAll({ showStatus: true, force: true }));
      });

      addControllerButton.addEventListener("click", startControllerDraft);
      addDriverButton.addEventListener("click", startDriverDraft);
      addGroupButton.addEventListener("click", startGroupDraft);
      addSwitchButton.addEventListener("click", () => startSwitchDraft());
      if (addSsrButton) {
        addSsrButton.addEventListener("click", () => startSsrDraft());
      }

      controllersContainer.addEventListener("click", handleControllerClick);
      controllersContainer.addEventListener("change", handleControllerToggle);
      driversContainer.addEventListener("click", handleDriverClick);
      driversContainer.addEventListener("input", handleDriverInput);
      driversContainer.addEventListener("change", handleDriverInput);
      if (groupsContainer) {
        groupsContainer.addEventListener("input", handleGroupSliderInput);
        groupsContainer.addEventListener("change", handleGroupSliderChange);
        groupsContainer.addEventListener("pointerdown", handleGroupSliderPointerDown);
      }
groupDefsContainer.addEventListener("click", handleGroupClick);
      if (switchesContainer) {
        switchesContainer.addEventListener("click", handleSwitchClick);
        switchesContainer.addEventListener("change", handleButtonToggle);
      }
      if (ssrContainer) {
        ssrContainer.addEventListener("click", handleSsrClick);
        ssrContainer.addEventListener("change", handleSsrChange);
      }
      if (controllerMapContainer) {
        controllerMapContainer.addEventListener("click", handlePortClick);
        controllerMapContainer.addEventListener("keydown", handlePortKeydown);
      }
      if (buttonDiscoveriesContainer) {
        buttonDiscoveriesContainer.addEventListener("click", handleButtonDiscoveryClick);
      }
      window.addEventListener("pointerup", handleGroupSliderPointerUp);
      window.addEventListener("pointercancel", handleGroupSliderPointerUp);
      if (patchPanelPortsContainer) {
        patchPanelPortsContainer.addEventListener("click", handlePatchPanelPortClick);
        patchPanelPortsContainer.addEventListener("pointerenter", handlePatchPanelPortPointerEnter, true);
        patchPanelPortsContainer.addEventListener("pointermove", handlePatchPanelPortPointerMove, true);
        patchPanelPortsContainer.addEventListener("pointerleave", handlePatchPanelPortPointerLeave, true);
        patchPanelPortsContainer.addEventListener("focusin", handlePatchPanelPortFocus);
        patchPanelPortsContainer.addEventListener("focusout", handlePatchPanelPortBlur);
      }
      if (patchPanelDetails) {
        patchPanelDetails.addEventListener("submit", handlePatchPanelDetailsSubmit);
        patchPanelDetails.addEventListener("click", handlePatchPanelDetailsClick);
      }

let pendingSwitchSelectKey = null;

      async function refreshAll({ showStatus = false, force = false } = {}) {
        if (!state.entryId) {
          stopAutoRefresh();
          groupsContainer.innerHTML = "";
          if (switchesContainer) {
            switchesContainer.innerHTML = "";
          }
          if (buttonDiscoveriesContainer) {
            buttonDiscoveriesContainer.innerHTML = "";
            buttonDiscoveriesContainer.classList.add("hidden");
          }
          controllersContainer.innerHTML = "";
          if (controllerMapContainer) {
            controllerMapContainer.innerHTML = "";
          }
          driversContainer.innerHTML = "";
          groupDefsContainer.innerHTML = "";
          if (ssrContainer) {
            ssrContainer.innerHTML = "";
          }
          if (addSsrButton) {
            addSsrButton.disabled = true;
          }
          if (serialLogContainer) {
            serialLogContainer.innerHTML = "";
          }
          groupsContainer.innerHTML = "";
          statusEl.textContent = "";
          return;
        }

        if (!force && isEditing()) {
          return;
        }

        if (refreshInFlight) {
          pendingRefreshOptions = {
            showStatus: pendingRefreshOptions?.showStatus || showStatus,
            force: pendingRefreshOptions?.force || force,
          };
          return;
        }

        refreshInFlight = true;
        if (showStatus) {
          statusEl.textContent = "Loading...";
        }
        clearError();

        try {
          const [groupState, snapshot] = await Promise.all([
            apiRequest("GET", `/api/s2j_led_driver/${state.entryId}/state`),
            apiRequest("GET", `/api/s2j_led_driver/${state.entryId}/registry`),
          ]);

          state.groupState = groupState || {};
          state.controllers = clone(snapshot.controllers);
          state.drivers = clone(snapshot.drivers);
          state.groups = clone(snapshot.groups);
          state.switches = clone(snapshot.switches || []);
          state.buttons = clone(snapshot.buttons);
          state.ledOutputs = clone(snapshot.led_outputs);
          state.pendingLedPwm = {};
          state.learnedButtons = clone(snapshot.learned_buttons || []);
          const ssrSnapshot = snapshot.ssr || {};
          state.ssrBaseAddress = Number(ssrSnapshot.base_address) || 0;
          state.ssrEntries = clone(ssrSnapshot.entries || []);
          const patchPanelSnapshot = snapshot.patch_panel || {};
          state.patchPanelPorts = clone(patchPanelSnapshot.ports || []);
          if (
            state.selectedPatchPort &&
            !state.patchPanelPorts.some((port) => Number(port.port_number) === Number(state.selectedPatchPort))
          ) {
            state.selectedPatchPort = null;
          }

          pendingButtonToggles.forEach((_, buttonId) => clearButtonToggle(buttonId));
          pendingSsrToggles.forEach((_, ssrId) => clearSsrToggle(ssrId));

          syncEditingSets();
          renderAll();

          if (pendingSwitchSelectKey) {
            state.editing.switches.add(pendingSwitchSelectKey);
            pendingSwitchSelectKey = null;
            renderSwitches();
          }

          statusEl.textContent = new Date().toLocaleTimeString();
          startAutoRefresh();
        } finally {
          refreshInFlight = false;
          if (pendingRefreshOptions) {
            const next = pendingRefreshOptions;
            pendingRefreshOptions = null;
            await refreshAll(next);
          }
        }
      }

  function renderAll() {
    renderDashboard();
    renderGroups();
    renderSwitches();
    renderSwitchVisualization();
    renderControllers();
    renderAcsSensors();
        renderControllerPortMap();
        renderDrivers();
        renderGroupDefinitions();
        renderSsrs();
        renderPatchPanel();
        renderSerialLog();
      }

      function getControllerStatus(controller) {
        return controller?.metadata?.status || null;
      }

      function renderDashboard() {
        if (!dashboardContainer) {
          return;
        }

        const controllers = state.controllers.filter((controller) => !controller.__tempId);
        let totalPower = 0;
        let totalCurrent = 0;
        let totalVoltage = 0;
        let contributing = 0;
        let minVoltage = Infinity;
        let maxVoltage = -Infinity;

        controllers.forEach((controller) => {
          const status = getControllerStatus(controller);
          if (!status) {
            return;
          }
          const power = Number(status.total_power) || 0;
          const current = Number(status.total_current) || 0;
          const voltage = Number(status.total_voltage) || 0;
          if (power || current || voltage) {
            contributing += 1;
          }
          if (voltage < minVoltage) {
            minVoltage = voltage;
          }
          if (voltage > maxVoltage) {
            maxVoltage = voltage;
          }
          totalPower += power;
          totalCurrent += current;
          totalVoltage += voltage;
        });
        totalVoltage = contributing > 0 ? totalVoltage / contributing : 0;

        if (!contributing) {
          dashboardContainer.innerHTML = '<p class="hint">Enable status polling to view live power data.</p>';
          return;
        }

        dashboardContainer.innerHTML = `
          <div class="dashboard-metric">
            <span>Total Power</span>
            <strong>${formatNumber(totalPower, 2)}W</strong>
          </div>
          <div class="dashboard-metric">
            <span>Total Current</span>
            <strong>${formatNumber(totalCurrent, 2)}A</strong>
          </div>
          <div class="dashboard-metric">
            <span>Voltage</span>
            <strong>${formatNumber(totalVoltage, 2)}V min:${formatNumber(minVoltage, 2)}V max:${formatNumber(maxVoltage, 2)}V</strong>
          </div>
        `;
      }

      function renderAcsSensors() {
        if (!acsContainer) {
          return;
        }

        const rows = [];
        state.controllers
          .filter((controller) => !controller.__tempId)
          .forEach((controller) => {
            const status = getControllerStatus(controller);
            const acs = status?.acs || [];
            acs.forEach((sensor) => {
              const bus = sensor.bus || "—";
              const voltage = formatNumber(Number(sensor.voltage), 2);
              const current = formatNumber(Number(sensor.current), 2);
              const power = formatNumber(Number(sensor.power), 2);
              const ready = sensor.ready ? "Ready" : "Not Ready";
              const valid = sensor.valid ? "Valid" : "Invalid";
              rows.push(
                `<tr>
                  <td>${bus}</td>
                  <td>${controller.name || controller.id || "Controller"}</td>
                  <td>${voltage}</td>
                  <td>${current}</td>
                  <td>${power}</td>
                  <td class="${sensor.ready ? "acs-status-ready" : "acs-status-fault"}">${ready}</td>
                  <td>${valid}</td>
                </tr>`
              );
            });
          });

        if (!rows.length) {
          acsContainer.innerHTML = '<p class="hint">No ACS sensor data available yet.</p>';
          return;
        }

        acsContainer.innerHTML = `
          <table class="acs-table">
            <thead>
              <tr>
                <th>Wire</th>
                <th>Controller</th>
                <th>Voltage (V)</th>
                <th>Current (A)</th>
                <th>Power (W)</th>
                <th>Status</th>
                <th>Sample</th>
              </tr>
            </thead>
            <tbody>
              ${rows.join("")}
            </tbody>
          </table>
        `;
      }

      function renderControllers() {
        const controllers = [...state.drafts.controllers, ...state.controllers];
        syncEditingSet("controllers", controllers);
        if (!controllers.length) {
          controllersContainer.innerHTML = '<p class="hint">No controllers defined yet.</p>';
          return;
        }

        const summaryRows = [];
        const editors = [];

        controllers.forEach((controller) => {
          const key = getItemKey(controller);
          const isDraft = Boolean(controller.__tempId);
          if (state.editing.controllers.has(key)) {
            editors.push(renderControllerEditor(controller, key, isDraft));
          } else {
            summaryRows.push(renderControllerSummaryRow(controller, key, isDraft));
          }
        });

        const tableHtml = summaryRows.length
          ? `
            <div class="controller-table-wrapper">
              <table class="controller-table">
                ${renderControllerHeader()}
                <tbody>
                  ${summaryRows.join("")}
                </tbody>
              </table>
            </div>
          `
          : "";

        controllersContainer.innerHTML = `${tableHtml}${editors.join("")}`;
      }

      function renderControllerPortMap() {
        if (!controllerMapContainer) {
          return;
        }

        if (!state.controllers.length) {
          controllerMapContainer.innerHTML = '<p class="hint">Add a controller to see its port layout.</p>';
          return;
        }

        if (!svgPortCache.top || !svgPortCache.bottom) {
          controllerMapContainer.innerHTML = '<p class="hint">Loading controller ports…</p>';
          ensureSvgTemplates()
            .then(() => renderControllerPortMap())
            .catch((error) => {
              console.error("Failed to load RJ45 SVG templates", error);
              controllerMapContainer.innerHTML =
                '<p class="error">Unable to load port graphics. Check browser console for details.</p>';
            });
          return;
        }

        controllerMapContainer.innerHTML = "";

        state.controllers.forEach((controller) => {
          const card = document.createElement("div");
          card.className = "controller-map-card";
          card.dataset.controllerId = controller.id || "";

          const header = document.createElement("div");
          header.className = "controller-map-header";
          const title = document.createElement("h3");
          title.textContent = controller.name || controller.id || "Controller";
          header.appendChild(title);

          const subtitle = document.createElement("span");
          subtitle.className = "hint";
          const baud = controller.baudrate || 115200;
          subtitle.textContent = controller.port ? `${controller.port} · baud ${baud}` : `Baud ${baud}`;
          header.appendChild(subtitle);
          card.appendChild(header);

          const rowsElement = document.createElement("div");
          rowsElement.className = "port-rows";

          const driverByIndex = new Map();
          state.drivers
            .filter((driver) => driver.controller_id === controller.id)
            .forEach((driver) => {
              const index = Number(driver.driver_index) || 0;
              if (!driverByIndex.has(index)) {
                driverByIndex.set(index, driver);
              }
            });

          [0, 1].forEach((rowIndex) => {
            const row = document.createElement("div");
            row.className = `port-row ${rowIndex === 0 ? "port-row-top" : "port-row-bottom"}`;
            for (let col = 0; col < 8; col += 1) {
              const driverIndex = rowIndex === 0 ? col : col + 8;
              const driver = driverByIndex.get(driverIndex);
              const cell = createPortCell(controller, driver, driverIndex, rowIndex === 0 ? "top" : "bottom");
              if (rowIndex === 0) {
                cell.classList.add("port-cell--top");
              } else {
                cell.classList.add("port-cell--bottom");
              }
              row.appendChild(cell);
            }
            rowsElement.appendChild(row);
          });

          card.appendChild(rowsElement);
          controllerMapContainer.appendChild(card);
        });
      }

      function renderDrivers() {
        const drivers = [...state.drafts.drivers, ...state.drivers];
        syncEditingSet("drivers", drivers);
        if (!drivers.length) {
          driversContainer.innerHTML = '<p class="hint">No drivers defined yet.</p>';
          return;
        }

        const summaryRows = [];
        const editors = [];

        drivers.forEach((driver) => {
          const key = getItemKey(driver);
          const isDraft = Boolean(driver.__tempId);
          if (state.editing.drivers.has(key)) {
            editors.push(renderDriverEditor(driver, key, isDraft));
          } else {
            summaryRows.push(renderDriverSummaryRow(driver, key, isDraft));
          }
        });

        const tableHtml = summaryRows.length
          ? `
              <div class="driver-table-wrapper">
                <table class="driver-table">
                  ${renderDriverHeader()}
                  <tbody>
                    ${summaryRows.join("")}
                  </tbody>
                </table>
              </div>
            `
          : "";

        driversContainer.innerHTML = `${tableHtml}${editors.join("")}`;
        refreshLedNameValidation();
      }

      function renderGroupDefinitions() {
        const groups = [...state.drafts.groups, ...state.groups];
        syncEditingSet("groups", groups);
        if (!groups.length) {
          groupDefsContainer.innerHTML = '<p class="hint">No group definitions yet.</p>';
          return;
        }

        const summaryRows = [];
        const editors = [];

        groups.forEach((group) => {
          const key = getItemKey(group);
          const isDraft = Boolean(group.__tempId);
          if (state.editing.groups.has(key)) {
            editors.push(renderGroupEditor(group, key, isDraft));
          } else {
            summaryRows.push(renderGroupDefinitionRow(group, key, isDraft));
          }
        });

        const tableHtml = summaryRows.length
          ? `
              <div class="group-def-table-wrapper">
                <table class="group-def-table">
                  ${renderGroupDefinitionHeader()}
                  <tbody>
                    ${summaryRows.join("")}
                  </tbody>
                </table>
              </div>
            `
          : "";

        groupDefsContainer.innerHTML = `${tableHtml}${editors.join("")}`;
      }

      function renderSerialLog() {
        if (!serialLogContainer) {
          return;
        }

        const controllers = state.controllers.filter((controller) => !controller.__tempId);
        if (!controllers.length) {
          serialLogContainer.innerHTML = '<p class="hint">Add a controller to view serial activity.</p>';
          return;
        }

        const sections = controllers
          .map((controller) => {
            const logEntries = (controller.metadata?.serial_log || []).slice(-50).reverse();
            const controllerName = controller.name || controller.id || "Controller";
            const lastEntry = logEntries[0];
            const subtitle = lastEntry
              ? `Last entry · ${formatTimestamp(lastEntry.timestamp)}`
              : "No serial activity captured yet.";
            const list = logEntries.length
              ? logEntries
                  .map((entry) => {
                    const direction = String(entry.direction || "rx").toLowerCase();
                    const timestamp = formatTimestamp(entry.timestamp);
                    let payloadText = "";
                    try {
                      payloadText = JSON.stringify(entry.payload ?? {}, null, 2);
                    } catch (error) {
                      payloadText = String(entry.payload ?? "");
                    }
                    return `
                      <div class="serial-log-entry serial-log-entry--${direction}">
                        <span class="serial-log-entry-meta">${escapeHtml(direction.toUpperCase())} · ${escapeHtml(
                      timestamp
                    )}</span>
                        <code>${escapeHtml(payloadText)}</code>
                      </div>
                    `;
                  })
                  .join("")
              : '<p class="hint">No serial traffic recorded.</p>';

            return `
              <div class="serial-log-group">
                <header>
                  <h3>${escapeHtml(controllerName)}</h3>
                  <span class="hint">${escapeHtml(subtitle)}</span>
                </header>
                <div class="serial-log-entries">
                  ${list}
                </div>
              </div>
            `;
          })
          .join("");

        serialLogContainer.innerHTML = sections;
      }

      function renderGroups() {
        if (!groupsContainer) {
          return;
        }
        if (state.activeGroupSlider) {
          return;
        }
        const groups = state.groups.filter((group) => !group.__tempId);
        if (!groups.length) {
          groupsContainer.innerHTML = '<p class="hint">No groups configured yet.</p>';
          return;
        }

        const rows = groups.map((group) => renderGroupStatusRow(group)).join("");
        groupsContainer.innerHTML = `
          <div class="group-table-wrapper">
            <table class="group-table">
              ${renderGroupStatusHeader()}
              <tbody>
                ${rows}
              </tbody>
            </table>
          </div>
        `;
      }

      function renderGroupStatusHeader() {
        return `
          <thead>
            <tr>
              <th>Name</th>
              <th>Members</th>
              <th>Brightness</th>
              <th>Status</th>
            </tr>
          </thead>
        `;
      }

      function renderGroupStatusRow(group) {
        const memberNames = getGroupMemberNames(group);
        const memberCount = memberNames.length;
        const preview = memberNames.slice(0, 4).join(", ");
        const memberLabel =
          memberCount === 0
            ? "No members linked"
            : `${memberCount} member${memberCount === 1 ? "" : "s"}${preview ? ` · ${preview}${memberCount > 4 ? "…" : ""}` : ""}`;

        const brightnessValue = Number.isFinite(group.brightness) ? group.brightness : 0;
        const sliderValue = Math.max(brightnessValue, 1);
        const stateEntry = state.groupState?.[group.id] || {};
        const isOn = Boolean(stateEntry.is_on ?? group.is_on);
        const faultyCount = Array.isArray(stateEntry.faulty_leds) ? stateEntry.faulty_leds.length : 0;
        const statusParts = [isOn ? "On" : "Off"];
        if (faultyCount) {
          statusParts.push(`Faulty ${faultyCount}`);
        }
        const statusLabel = statusParts.join(" · ");

        return `
          <tr data-key="${group.id}">
            <td>${group.name || group.id || "Group"}</td>
            <td>${memberLabel}</td>
            <td class="group-slider">
              <input type="range" min="1" max="100" value="${sliderValue}" data-action="group-slider" data-group-id="${group.id}" />
              <span class="group-slider-value">${sliderValue}%</span>
            </td>
            <td>${statusLabel}</td>
          </tr>
        `;
      }

      function getControllerLedStats(controllerId) {
        if (!controllerId) {
          return { total: 0, on: 0, fault: 0 };
        }
        let total = 0;
        let onCount = 0;
        let faultCount = 0;
        state.drivers.forEach((driver) => {
          if (driver.controller_id !== controllerId) {
            return;
          }
          (driver.outputs || []).forEach((output) => {
            if (output.disabled) {
              return;
            }
            total += 1;
            if (Number(output.level || 0) > 0) {
              onCount += 1;
            }
            if (output.faulty) {
              faultCount += 1;
            }
          });
        });
        return { total, on: onCount, fault: faultCount };
      }


      function renderSwitches() {
        if (!switchesContainer) {
          if (buttonDiscoveriesContainer) {
            buttonDiscoveriesContainer.innerHTML = "";
            buttonDiscoveriesContainer.classList.add("hidden");
          }
          return;
        }

        const entries = [...state.switches, ...state.drafts.switches];
        entries.sort((a, b) => {
          const switchA = safeNumericSwitch(a.switch);
          const switchB = safeNumericSwitch(b.switch);
          if (Number.isFinite(switchA) && Number.isFinite(switchB) && switchA !== switchB) {
            return switchA - switchB;
          }
          if (Number.isFinite(switchA) !== Number.isFinite(switchB)) {
            return Number.isFinite(switchA) ? -1 : 1;
          }
          const draftA = a.__tempId ? 1 : 0;
          const draftB = b.__tempId ? 1 : 0;
          if (draftA !== draftB) {
            return draftA - draftB;
          }
          return (a.name || "").localeCompare(b.name || "");
        });

        syncEditingSet("switches", entries);

        if (!entries.length) {
          switchesContainer.innerHTML = '<p class="hint">No switches configured yet.</p>';
          renderButtonDiscoveries();
          return;
        }

        switchesContainer.innerHTML = entries
          .map((entry) => {
            const key = getItemKey(entry);
            const isDraft = Boolean(entry.__tempId);
            return state.editing.switches.has(key)
              ? renderSwitchEditor(entry, key, isDraft)
              : renderSwitchSummary(entry, key, isDraft);
          })
          .join("");

        renderButtonDiscoveries();
      }

      function renderSwitchVisualization() {
        if (!switchVisualSelect) {
          return;
        }
        const entries = state.switches || [];
        if (!entries.length) {
          switchVisualSelect.innerHTML = '<option value="">No switches configured</option>';
          switchVisualSelect.disabled = true;
          return;
        }
        const sorted = entries.slice().sort((a, b) => {
          const aVal = safeNumericSwitch(a.switch);
          const bVal = safeNumericSwitch(b.switch);
          if (Number.isFinite(aVal) && Number.isFinite(bVal) && aVal !== bVal) {
            return aVal - bVal;
          }
          return (a.name || "").localeCompare(b.name || "");
        });
        const options = ['<option value="">Select a switch</option>'].concat(
          sorted.map((entry) => {
            const switchValue = entry.switch ?? "";
            const label =
              entry.name ||
              (Number.isFinite(Number(switchValue)) ? `Switch ${switchValue}` : String(switchValue || "Switch"));
            return `<option value="${switchValue}">${escapeHtml(label)}</option>`;
          })
        );
        switchVisualSelect.innerHTML = options.join("");
        switchVisualSelect.disabled = false;
      }

      function safeNumericSwitch(value) {
        const numeric = Number(value);
        return Number.isFinite(numeric) ? numeric : NaN;
      }

      function renderSwitchSummary(entry, key, isDraft) {
        const switchValue = entry.switch ?? "";
        const buttonCount = Number(entry.button_count ?? 0);
        const hasBuzzer = entry.has_buzzer ? "Yes" : "No";
        const flashLeds = entry.flash_leds === false ? "No" : "Yes";
        const typeLabel = (entry.type || "momentary").toString();
        const buttons = getButtonsForSwitch(entry);

        return `
          <div class="switch-card" data-key="${key}">
            <div class="switch-card-header">
              <div class="switch-card-inline">
                <h3>${entry.name || (Number.isFinite(Number(switchValue)) ? `Switch ${switchValue}` : "Switch")}</h3>
                <span class="switch-chip">ID: ${switchValue === "" ? "—" : switchValue}</span>
                <span class="switch-chip">Type: ${typeLabel}</span>
                <span class="switch-chip">Buttons: <strong>${buttonCount || buttons.length}</strong></span>
                <span class="switch-chip">Buzzer: ${hasBuzzer}</span>
                <span class="switch-chip">Flash LEDs: ${flashLeds}</span>
              </div>
              <div class="button-row-actions">
                <button class="secondary" data-action="edit-switch" data-key="${key}" data-draft="${isDraft}">Edit</button>
                <button class="danger" data-action="delete-switch" data-key="${key}" data-draft="${isDraft}">${
                  isDraft ? "Discard" : "Delete"
                }</button>
              </div>
            </div>
            ${renderSwitchButtonsTable(buttons, entry, key, false)}
          </div>
        `;
      }

      function renderSwitchEditor(entry, key, isDraft) {
        const switchValue = entry.switch ?? "";
        const nameValue = entry.name || "";
        const typeValue = entry.type || "momentary";
        const buttonCount = Number(entry.button_count ?? 5);
        const hasBuzzer = entry.has_buzzer ? "checked" : "";
        const flashLeds = entry.flash_leds === false ? "" : "checked";
        const metadataJson = escapeHtml(JSON.stringify(entry.metadata || {}));
        const buttons = getButtonsForSwitch(entry);
        const showButtonEditor = Boolean(entry.id);
        const canAddButton = Boolean(entry.id) && buttons.length < buttonCount;

        return `
          <div class="switch-card editor" data-key="${key}" data-id="${entry.id || ""}" data-draft="${isDraft}" data-metadata="${metadataJson}">
            <div class="switch-card-header">
              <div class="switch-card-inline">
                <h3>${isDraft ? "New Switch" : entry.name || (Number.isFinite(Number(switchValue)) ? `Switch ${switchValue}` : "Switch")}</h3>
                <span class="switch-chip">ID: ${switchValue === "" ? "—" : switchValue}</span>
                <span class="switch-chip">Type: ${typeValue}</span>
                <span class="switch-chip">Configured Buttons: ${buttonCount}</span>
              </div>
              <div class="button-row-actions">
                <button class="primary" data-action="save-switch" data-key="${key}" data-draft="${isDraft}">Save</button>
                <button class="secondary" data-action="cancel-switch" data-key="${key}" data-draft="${isDraft}">Cancel</button>
              </div>
            </div>
            <div class="two-column" style="margin-bottom: 1rem;">
              <label>
                Name
                <input type="text" data-field="name" value="${nameValue}" placeholder="Friendly name" />
              </label>
              <label>
                Switch ID
                <input type="number" data-field="switch" min="0" step="1" value="${switchValue === "" ? "" : Number(switchValue)}" />
              </label>
              <label>
                Type
                <select data-field="type">
                  ${buildSwitchTypeOptions(typeValue)}
                </select>
              </label>
              <label>
                Button Count
                <input type="number" data-field="button_count" min="1" max="5" value="${buttonCount}" />
              </label>
              <label>
                Buzzer Installed
                <input type="checkbox" data-field="has_buzzer" ${hasBuzzer} />
              </label>
              <label>
                Flash LEDs
                <input type="checkbox" data-field="flash_leds" ${flashLeds} />
              </label>
            </div>
            ${
              showButtonEditor
                ? `
            <div class="inline" style="justify-content: flex-end; margin-bottom: 0.5rem;">
              <button class="secondary" data-action="add-button" data-switch-key="${key}" ${canAddButton ? "" : "disabled"}>Add Button</button>
            </div>
            ${renderSwitchButtonsTable(buttons, entry, key, true)}
            ${
              !canAddButton
                ? '<p class="hint">Maximum number of buttons configured.</p>'
                : ""
            }`
                : '<p class="hint">Save this switch before assigning buttons.</p>'
            }
          </div>
        `;
      }

      function renderSwitchButtonsTable(buttons, entry, switchKey, editable) {
        const rows = buttons.length
          ? buttons.map((button) => renderButtonRow(button, entry, switchKey, editable)).join("")
          : '<tr><td colspan="5" class="hint">No buttons assigned yet.</td></tr>';

        const hasEditing = buttons.some((button) => state.editing.buttons.has(getItemKey(button)));
        const hasDrafts = state.drafts.buttons.some((draft) => draft.__parentKey === switchKey);
        const disableBulkActions = !(hasEditing || hasDrafts);

        const table = `
          <table class="button-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Mask</th>
                <th>Group</th>
                <th>State</th>
                ${editable ? '<th class="row-actions">Actions</th>' : ""}
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        `;

        const actions =
          !editable || disableBulkActions
            ? ""
            : `
          <div class="button-row-actions master">
            <button class="primary" data-action="save-buttons" data-switch-key="${switchKey}">Save Buttons</button>
            <button class="secondary" data-action="cancel-buttons" data-switch-key="${switchKey}">Cancel</button>
          </div>
        `;

        return `<div class="button-table-wrapper">${table}${actions}</div>`;
      }

      function renderControllerHeader() {
        return `
          <thead>
            <tr>
              <th>Name</th>
              <th>Serial Port</th>
              <th>Baud</th>
              <th>CAN Interface</th>
              <th class="controller-actions-header">Actions</th>
            </tr>
          </thead>
        `;
      }

      function renderControllerSummaryRow(controller, key, isDraft) {
        const metadataCount = controller.metadata ? Object.keys(controller.metadata).length : 0;
        const baud = controller.baudrate || 115200;
        const metaLabel = metadataCount ? `${metadataCount} meta` : "";
        const pollingEnabled = Boolean(controller.polling_enabled);
        const canInterface = Boolean(controller.has_can_interface);
        const activeCanId = state.controllers.find((entry) => entry.has_can_interface)?.id || null;
        const disableCanToggle = Boolean(activeCanId && controller.id !== activeCanId);
        const pollToggle = isDraft
          ? ""
          : `<label class="controller-poll-toggle">
                <input type="checkbox" data-action="toggle-poll" data-controller-id="${controller.id}"${pollingEnabled ? " checked" : ""} />
                <span>Status Poll</span>
              </label>`;
        const canToggle = isDraft
          ? ""
          : `<label class="controller-poll-toggle">
                <input type="checkbox" data-action="toggle-can" data-controller-id="${controller.id}"${canInterface ? " checked" : ""}${
              disableCanToggle ? " disabled" : ""
            } />
              </label>`;
        const canInterfaceCell = isDraft ? (canInterface ? "Enabled" : "—") : canToggle;

        const ledStats = getControllerLedStats(controller.id);
        const statsLabel = ledStats.total
          ? `LEDs On ${ledStats.on}/${ledStats.total}${ledStats.fault ? ` · Fault ${ledStats.fault}` : ""}`
          : "No LEDs linked";
        const controllerStatus = getControllerStatus(controller);
        const powerValue = Number(controllerStatus?.total_power);
        const powerDisplay = Number.isFinite(powerValue) ? `${formatNumber(powerValue, 2)} W` : null;
        const metaDetails = [powerDisplay, statsLabel].filter(Boolean).join(" · ");

        const actions = isDraft
          ? ""
          : `
              <div class="controller-actions">
                ${pollToggle}
                <div class="row-actions">
                  <button class="secondary" data-action="edit-controller" data-key="${key}" data-draft="${isDraft}">Edit</button>
                  <button class="danger" data-action="delete-controller" data-key="${key}" data-draft="${isDraft}">${
              isDraft ? "Discard" : "Delete"
            }</button>
                </div>
              </div>
            `;

        return `
          <tr data-key="${key}">
            <td>
              ${isDraft ? "New Controller" : controller.name || controller.id || "Controller"}
              ${metaDetails ? `<span class="hint controller-meta">${metaDetails}</span>` : ""}
            </td>
            <td>${controller.port || "—"}</td>
            <td>${baud}${metaLabel ? ` · ${metaLabel}` : ""}</td>
            <td>${canInterfaceCell}</td>
            <td class="controller-actions-cell">
              ${actions}
            </td>
          </tr>
        `;
      }

      function renderControllerEditor(controller, key, isDraft) {
        const activeCanId = state.controllers.find((entry) => entry.has_can_interface)?.id || null;
        const disableCanCheckbox = Boolean(
          activeCanId && controller.id && controller.id !== activeCanId
        );
        return `
          <div class="list-row editor" data-key="${key}">
            <div class="card editor" data-id="${controller.id || ""}" data-draft="${isDraft}">
              <div class="stack">
                <h4>${isDraft ? "New Controller" : controller.name || controller.id || "Controller"}</h4>
                <div class="two-column">
                  <label>
                    Name
                    <input data-field="name" value="${controller.name || ""}" />
                  </label>
                  <label>
                    Port
                    <input data-field="port" value="${controller.port || ""}" placeholder="/dev/ttyUSB0" />
                  </label>
                  <label>
                    Baudrate
                    <input data-field="baudrate" type="number" value="${controller.baudrate || 115200}" />
                  </label>
                  <label>
                    Metadata (JSON)
                    <textarea data-field="metadata">${JSON.stringify(controller.metadata || {}, null, 2)}</textarea>
                  </label>
                  <label class="inline-checkbox">
                    <input type="checkbox" data-field="has_can_interface"${controller.has_can_interface ? " checked" : ""}${
          disableCanCheckbox ? " disabled" : ""
        } />
                    <span>CAN Interface</span>
                  </label>
                </div>
                <div class="inline">
                  <button class="primary" data-action="save-controller" data-key="${key}" data-draft="${isDraft}">Save</button>
                  <button class="secondary" data-action="cancel-controller" data-key="${key}" data-draft="${isDraft}">Cancel</button>
                  <button class="danger" data-action="delete-controller" data-key="${key}" data-draft="${isDraft}">${
                    isDraft ? "Discard" : "Delete"
                  }</button>
                </div>
              </div>
            </div>
          </div>
        `;
      }

      function renderDriverHeader() {
        return `
          <thead>
            <tr>
              <th>Name</th>
              <th>Controller</th>
              <th>LED Outputs</th>
              <th class="driver-actions-header">Actions</th>
            </tr>
          </thead>
        `;
      }

      function renderDriverSummaryRow(driver, key, isDraft) {
        const controllerName = getControllerDisplayName(driver.controller_id) || "Unassigned";
        const outputs = ensureDriverOutputsClone(driver);
        const previewNames = outputs.map((output) => output.name || `LED ${output.slot + 1}`);
        const preview = previewNames.slice(0, 4).join(", ");
        return `
          <tr data-key="${key}">
            <td>${isDraft ? "New Driver" : driver.name || driver.id || "Driver"}</td>
            <td>${controllerName} · Index ${driver.driver_index ?? 0}</td>
            <td>${preview}</td>
            <td class="driver-actions-cell">
              <div class="row-actions">
                <button class="secondary" data-action="edit-driver" data-key="${key}" data-draft="${isDraft}">Edit</button>
                <button class="danger" data-action="delete-driver" data-key="${key}" data-draft="${isDraft}">${
                  isDraft ? "Discard" : "Delete"
                }</button>
              </div>
            </td>
          </tr>
        `;
      }

      function renderDriverEditor(driver, key, isDraft) {
        const controllerOptions = state.controllers
          .map(
            (controller) =>
              `<option value="${controller.id}"${controller.id == driver.controller_id ? " selected" : ""}>${
                controller.name || controller.id
              }</option>`
          )
          .join("");

        const controllerField = state.controllers.length
          ? `<select data-field="controller_id"><option value="">Select</option>${controllerOptions}</select>`
          : `<div class="hint">Add a controller first to assign drivers.</div>`;

        const outputs = ensureDriverOutputsClone(driver);
        const outputCards = outputs
          .map((output, index) => renderDriverOutputCard(output, index, driver.__tempId || driver.id || key))
          .join("");

        return `
          <div class="list-row editor" data-key="${key}">
            <div class="card editor" data-id="${driver.id || ""}" data-draft="${isDraft}">
              <div class="stack">
                <h4>${isDraft ? "New Driver" : driver.name || driver.id || "Driver"}</h4>
                <div class="two-column">
                  <label>
                    Name
                    <input data-field="driver_name" value="${driver.name || ""}" />
                  </label>
                  <label>
                    Controller
                    ${controllerField}
                  </label>
                  <label>
                    Driver Index
                    <input data-field="driver_index" type="number" value="${driver.driver_index ?? 0}" />
                  </label>
                </div>
                <div class="driver-output-grid">
                  ${outputCards}
                </div>
                <div class="inline">
                  <button class="primary" data-action="save-driver" data-key="${key}" data-draft="${isDraft}">Save</button>
                  <button class="secondary" data-action="cancel-driver" data-key="${key}" data-draft="${isDraft}">Cancel</button>
                  <button class="danger" data-action="delete-driver" data-key="${key}" data-draft="${isDraft}">${
                    isDraft ? "Discard" : "Delete"
                  }</button>
                </div>
              </div>
            </div>
          </div>
        `;
      }

      function renderDriverOutputCard(output, index, driverKey) {
        const isOn = Number(output.level ?? 0) > 0;
        const faulty = Boolean(output.faulty);
        const currentPwm = Number.isFinite(Number(output.pwm)) ? Number(output.pwm) : 0;
        const targetPwm = Number.isFinite(Number(output.target_pwm)) ? Number(output.target_pwm) : currentPwm;
        const channelsData = Array.isArray(output.channels) ? output.channels.join(",") : "";

        return `
          <div class="driver-output-card${output.disabled ? " disabled" : ""}"
               data-slot="${output.slot}"
               data-output-id="${output.id || ""}"
               data-driver-key="${driverKey}"
               data-output-pwm="${currentPwm}"
               data-output-target-pwm="${targetPwm}"
               data-output-level="${output.level ?? 0}"
               data-output-disabled="${output.disabled ? "true" : "false"}"
               data-output-faulty="${faulty ? "true" : "false"}"
               data-output-channels="${channelsData}">
            <h5>LED ${index + 1}</h5>
            <label>
              Name
              <input data-output-field="name" value="${output.name || ""}" />
            </label>
            <label class="inline-checkbox">
              <input type="checkbox" data-output-field="disabled"${output.disabled ? " checked" : ""} />
              <span>Disabled</span>
            </label>
            <div class="output-status-grid">
              <label class="inline-checkbox readonly">
                <input type="checkbox" data-display="current-on" disabled${isOn ? " checked" : ""} />
                <span>Currently On</span>
              </label>
              <label class="inline-checkbox readonly">
                <input type="checkbox" data-display="current-fault" disabled${faulty ? " checked" : ""} />
                <span>Fault</span>
              </label>
            </div>
            <label>
              Current PWM
              <input data-display="current-pwm" type="number" value="${currentPwm}" readonly />
            </label>
            <label>
              Target PWM
              <input data-display="target-pwm" type="number" value="${targetPwm}" readonly />
            </label>
            <label>
              Min PWM
              <input data-output-field="min_pwm" type="number" value="${output.min_pwm ?? 0}" />
            </label>
            <label>
              Max PWM
              <input data-output-field="max_pwm" type="number" value="${output.max_pwm ?? 255}" />
            </label>
          </div>
        `;
      }

      function renderGroupDefinitionHeader() {
        return `
          <thead>
            <tr>
              <th>Name</th>
              <th>Members</th>
              <th>Brightness</th>
              <th>Default State</th>
              <th class="group-def-actions-header">Actions</th>
            </tr>
          </thead>
        `;
      }

      function renderGroupDefinitionRow(group, key, isDraft) {
        const memberNames = getGroupMemberNames(group);
        const memberCount = memberNames.length;
        const preview = memberNames.slice(0, 4).join(", ");
        const memberLabel =
          memberCount === 0
            ? "No members linked"
            : `${memberCount} member${memberCount === 1 ? "" : "s"}`;
        const brightnessValue = Number.isFinite(group.brightness) ? group.brightness : 0;
        const defaultState = group.is_on ? "On" : "Off";
        return `
          <tr data-key="${key}">
            <td>${isDraft ? "New Group" : group.name || group.id || "Group"}</td>
            <td>${memberLabel}${preview ? ` · ${preview}${memberCount > 4 ? "…" : ""}` : ""}</td>
            <td>${brightnessValue}%</td>
            <td>${defaultState}</td>
            <td class="group-def-actions-cell">
              <div class="row-actions">
                <button class="secondary" data-action="edit-group" data-key="${key}" data-draft="${isDraft}">Edit</button>
                <button class="danger" data-action="delete-group" data-key="${key}" data-draft="${isDraft}">${
                  isDraft ? "Discard" : "Delete"
                }</button>
              </div>
            </td>
          </tr>
        `;
      }

      function renderGroupEditor(group, key, isDraft) {
        const selectedSsrIds = getGroupSsrIds(group.id);
        const optionCount = state.ledOutputs.length + state.ssrEntries.length;
        const selectSize = Math.min(Math.max(optionCount || 4, 4), 12);
        const ledOptionsHtml = buildLedOptionsHtml(group.led_ids || [], {
          includeSsr: true,
          selectedSsrIds,
        });

        const hasOptions = optionCount > 0;
        const ledField = hasOptions
          ? `<select data-field="led_ids" multiple size="${selectSize}">${ledOptionsHtml}</select>
              <span class="hint">Hold Cmd/Ctrl to select multiple members.</span>`
          : `<div class="hint">Add drivers or SSR outputs to assign members to this group.</div>`;

        return `
          <div class="list-row editor" data-key="${key}">
            <div class="card editor" data-id="${group.id || ""}" data-draft="${isDraft}">
              <div class="stack">
                <h4>${isDraft ? "New Group" : group.name || group.id || "Group"}</h4>
                <div class="two-column">
                  <label>
                    Name
                    <input data-field="name" value="${group.name || ""}" />
                  </label>
                  <label>
                    Members
                    ${ledField}
                  </label>
                  <label>
                    Brightness
                    <input data-field="brightness" type="number" value="${group.brightness ?? 0}" />
                  </label>
                  <label>
                    Default State
                    <select data-field="is_on">
                      <option value="false"${group.is_on ? "" : " selected"}>Off</option>
                      <option value="true"${group.is_on ? " selected" : ""}>On</option>
                    </select>
                  </label>
                </div>
                <div class="inline">
                  <button class="primary" data-action="save-group" data-key="${key}" data-draft="${isDraft}">Save</button>
                  <button class="secondary" data-action="cancel-group" data-key="${key}" data-draft="${isDraft}">Cancel</button>
                  <button class="danger" data-action="delete-group" data-key="${key}" data-draft="${isDraft}">${
                    isDraft ? "Discard" : "Delete"
                  }</button>
                </div>
              </div>
            </div>
          </div>
        `;
      }

      function getCanInterfaceControllerId() {
        const controller = state.controllers.find((ctrl) => ctrl.has_can_interface);
        return controller?.id || null;
      }

      function renderSsrs() {
        if (!ssrContainer) {
          return;
        }
        const entries = [...state.ssrEntries, ...state.drafts.ssrs];
        syncEditingSet("ssrs", entries);

        const summaryRows = [];
        const editors = [];

        const sortedEntries = entries
          .filter((entry) => !entry.__tempId)
          .slice()
          .sort((a, b) => {
            const bitA = Number(a.bit_index ?? 0);
            const bitB = Number(b.bit_index ?? 0);
            return bitA - bitB;
          });

        sortedEntries.forEach((entry) => {
          const key = getItemKey(entry);
          if (state.editing.ssrs.has(key)) {
            return;
          }
          summaryRows.push(renderSsrSummaryRow(entry, key, false));
        });

        entries
          .filter((entry) => entry.__tempId || state.editing.ssrs.has(getItemKey(entry)))
          .forEach((entry) => {
            const key = getItemKey(entry);
            editors.push(renderSsrEditor(entry, key, Boolean(entry.__tempId)));
          });

        const gridHtml = summaryRows.length
          ? `<div class="ssr-grid">${summaryRows.join("")}</div>`
          : '<p class="hint">No SSR outputs configured.</p>';

        const canControllerId = getCanInterfaceControllerId();
        const baseHint = canControllerId
          ? ""
          : '<span class="hint error">Assign a controller as the CAN interface to send SSR commands.</span>';

        ssrContainer.innerHTML = `
          <div class="ssr-base-controls">
            <label>
              Base Address
              <input type="number" id="ssr-base-input" min="0" max="255" value="${state.ssrBaseAddress}" ${
          !state.entryId ? "disabled" : ""
        } />
            </label>
            <span class="hint">Shared across all SSR outputs.</span>
            ${baseHint}
          </div>
          ${gridHtml}
          ${editors.join("")}
        `;

        if (addSsrButton) {
          addSsrButton.disabled = entries.length >= SSR_MAX_ENTRIES || !state.entryId;
        }
      }

      function renderSsrSummaryRow(entry, key, isDraft) {
        const groupLabel = getGroupDisplayName(entry.group_id);
        const canControllerId = getCanInterfaceControllerId();
        const hasBaseAddress = state.ssrBaseAddress > 0;
        const isSaved = Boolean(entry.id);
        const pending = entry.id ? pendingSsrToggles.has(entry.id) : false;
        const toggleDisabled = !isSaved || !canControllerId || !hasBaseAddress || pending;
        const bitIndex = Number(entry.bit_index ?? 0);
        const imageSrc = entry.is_on ? "SSROn.jpg" : "SSR.jpg";
        const stateAttr = entry.is_on ? "on" : "off";

        return `
          <div class="ssr-tile" data-key="${key}">
            <button type="button"
                    class="ssr-image-button${toggleDisabled ? " disabled" : ""}"
                    data-action="toggle-ssr"
                    data-ssr-id="${entry.id || ""}"
                    data-current-state="${stateAttr}"${toggleDisabled ? " disabled" : ""}>
              <img src="${imageSrc}" alt="${entry.name || entry.id || "SSR"}" />
            </button>
            <div class="ssr-label">
              ${entry.name || entry.id || "SSR"}
              <small>${groupLabel} · Bit ${bitIndex}</small>
            </div>
            <div class="row-actions ssr-actions">
              <button class="secondary" data-action="edit-ssr" data-key="${key}" data-draft="${isDraft}">Edit</button>
              <button class="danger" data-action="delete-ssr" data-key="${key}" data-draft="${isDraft}">${
                isDraft ? "Discard" : "Delete"
              }</button>
            </div>
          </div>
        `;
      }

      function renderSsrEditor(entry, key, isDraft) {
        const availableBits = getAvailableSsrBits(entry.bit_index);
        const bitOptions = availableBits
          .map(
            (bit) => `<option value="${bit}"${Number(entry.bit_index) === bit ? " selected" : ""}>Bit ${bit}</option>`
          )
          .join("");

        return `
          <div class="list-row editor" data-key="${key}">
            <div class="card editor" data-id="${entry.id || ""}" data-draft="${isDraft}">
              <div class="stack">
                <h4>${isDraft ? "New SSR" : entry.name || entry.id || "SSR"}</h4>
                <div class="two-column">
                  <label>
                    Name
                    <input data-field="ssr_name" value="${entry.name || ""}" />
                  </label>
                  <label>
                    Bit Index
                    <select data-field="bit_index">
                      ${bitOptions}
                    </select>
                  </label>
                  <label>
                    Group
                    <select data-field="group_id">
                      ${buildGroupOptions(entry.group_id)}
                    </select>
                  </label>
                </div>
                <div class="inline">
                  <button class="primary" data-action="save-ssr" data-key="${key}" data-draft="${isDraft}">Save</button>
                  <button class="secondary" data-action="cancel-ssr" data-key="${key}" data-draft="${isDraft}">Cancel</button>
                  <button class="danger" data-action="delete-ssr" data-key="${key}" data-draft="${isDraft}">${
                    isDraft ? "Discard" : "Delete"
                  }</button>
                </div>
              </div>
            </div>
          </div>
        `;
      }

      function startSsrDraft(initial = {}) {
        if (!state.entryId) {
          setError("Select an integration entry before adding SSR outputs.");
          return;
        }
        const totalEntries = state.ssrEntries.length + state.drafts.ssrs.length;
        if (totalEntries >= SSR_MAX_ENTRIES) {
          setError(`A maximum of ${SSR_MAX_ENTRIES} SSR outputs is supported.`);
          return;
        }
        const availableBits = getAvailableSsrBits();
        if (!availableBits.length) {
          setError("All SSR bit positions are already in use.");
          return;
        }
        const draft = {
          id: "",
          name: initial.name || `SSR ${availableBits[0] + 1}`,
          bit_index: initial.bit_index ?? availableBits[0],
          group_id: initial.group_id || "",
          is_on: Boolean(initial.is_on),
        };
        addDraft("ssrs", draft);
        clearError();
        renderSsrs();
      }

      function readSsrCard(card) {
        const nameInput = card.querySelector('[data-field="ssr_name"]');
        const bitInput = card.querySelector('[data-field="bit_index"]');
        const groupInput = card.querySelector('[data-field="group_id"]');
        const payload = {
          id: card.dataset.id || undefined,
          name: nameInput?.value.trim() || "",
          bit_index: Number(bitInput?.value),
          group_id: groupInput?.value || null,
        };
        if (!Number.isInteger(payload.bit_index) || payload.bit_index < 0 || payload.bit_index >= SSR_MAX_ENTRIES) {
          throw new Error("Select a valid bit index.");
        }
        return payload;
      }

      function handleSsrClick(event) {
        const button = event.target.closest("button[data-action]");
        if (!button) {
          return;
        }
        const action = button.dataset.action;
        if (action === "toggle-ssr") {
          handleSsrToggle(button);
          return;
        }
        const { key } = button.dataset;
        const isDraft = button.dataset.draft === "true";

        if (action === "edit-ssr") {
          state.editing.ssrs.add(key);
          renderSsrs();
          return;
        }

        if (action === "cancel-ssr") {
          state.editing.ssrs.delete(key);
          if (isDraft) {
            removeDraft("ssrs", key);
          }
          renderSsrs();
          return;
        }

        if (action === "delete-ssr") {
          if (isDraft) {
            removeDraft("ssrs", key);
            state.editing.ssrs.delete(key);
            renderSsrs();
            return;
          }
          const entry = state.ssrEntries.find((item) => getItemKey(item) === key);
          if (!entry) {
            return;
          }
          if (!state.entryId) {
            setError("Select an integration entry before deleting SSR outputs.");
            return;
          }
          if (!confirmDeletion("Delete this SSR output?")) {
            return;
          }
          withErrorNotice(async () => {
            await deleteSsrEntry(state.entryId, entry.id);
            await refreshAll({ showStatus: false, force: true });
          });
          return;
        }

        if (action === "save-ssr") {
          const card = button.closest(".card");
          if (!card) {
            return;
          }
          if (!state.entryId) {
            setError("Select an integration entry before saving SSR outputs.");
            return;
          }
          withErrorNotice(async () => {
            const payload = readSsrCard(card);
            state.editing.ssrs.delete(key);
            if (isDraft) {
              removeDraft("ssrs", key);
            }
            await upsertSsrEntry(state.entryId, payload);
            await refreshAll({ showStatus: false, force: true });
          });
        }
      }

      function handleSsrChange(event) {
        const target = event.target;
        if (!target) {
          return;
        }
        if (target.id === "ssr-base-input") {
          handleSsrBaseInput(target);
        }
      }

      function handleSsrBaseInput(input) {
        if (!input || !state.entryId) {
          input.value = state.ssrBaseAddress;
          return;
        }
        const nextValue = Number(input.value);
        if (!Number.isFinite(nextValue)) {
          input.value = state.ssrBaseAddress;
          return;
        }
        const clamped = Math.max(0, Math.min(255, nextValue));
        input.value = clamped;
        input.disabled = true;
        (async () => {
          try {
            await setSsrBaseAddress(state.entryId, clamped);
            await refreshAll({ showStatus: false, force: true });
          } catch (error) {
            console.error(error);
            setError(error.message || "Failed to update SSR base address");
            input.value = state.ssrBaseAddress;
          } finally {
            input.disabled = false;
          }
        })();
      }

      function handleSsrToggle(button) {
        if (!state.entryId) {
          setError("Select an integration entry before toggling SSR outputs.");
          return;
        }
        const ssrId = button.dataset.ssrId;
        if (!ssrId) {
          setError("Save this SSR definition before toggling it.");
          return;
        }
        if (!getCanInterfaceControllerId()) {
          setError("Assign a controller as the CAN interface to send SSR commands.");
          return;
        }
        if (state.ssrBaseAddress <= 0) {
          setError("Set the SSR base address before toggling outputs.");
          return;
        }

        const currentState = button.dataset.currentState === "on";
        const desired = !currentState;

        disableSsrToggle(ssrId, button);
        (async () => {
          try {
            await setSsrState(state.entryId, ssrId, desired);
            button.dataset.currentState = desired ? "on" : "off";
            const image = button.querySelector("img");
            if (image) {
              image.src = desired ? "SSROn.jpg" : "SSR.jpg";
            }
            await refreshAll({ showStatus: false, force: true });
          } catch (error) {
            console.error(error);
            setError(error.message || "Failed to toggle SSR output");
            button.dataset.currentState = currentState ? "on" : "off";
            const image = button.querySelector("img");
            if (image) {
              image.src = currentState ? "SSROn.jpg" : "SSR.jpg";
            }
          } finally {
            clearSsrToggle(ssrId, button);
          }
        })();
      }

      function disableSsrToggle(ssrId, element) {
        if (!ssrId) {
          return;
        }
        const existing = pendingSsrToggles.get(ssrId);
        if (existing?.timer) {
          clearTimeout(existing.timer);
        }
        const timeout = setTimeout(() => clearSsrToggle(ssrId), 5000);
        pendingSsrToggles.set(ssrId, { timer: timeout });
        const selector = `button[data-action="toggle-ssr"][data-ssr-id="${cssEscapeId(ssrId)}"]`;
        const button = element || document.querySelector(selector);
        if (button) {
          button.disabled = true;
          button.classList.add("disabled");
        }
      }

      function clearSsrToggle(ssrId, element) {
        if (!ssrId) {
          return;
        }
        const pending = pendingSsrToggles.get(ssrId);
        if (pending?.timer) {
          clearTimeout(pending.timer);
        }
        pendingSsrToggles.delete(ssrId);
        const selector = `button[data-action="toggle-ssr"][data-ssr-id="${cssEscapeId(ssrId)}"]`;
        const button = element || document.querySelector(selector);
        if (button) {
          button.disabled = false;
          button.classList.remove("disabled");
        }
      }

      function getAvailableSsrBits(currentBit) {
        const used = new Set(
          [...state.ssrEntries, ...state.drafts.ssrs]
            .map((entry) => Number(entry.bit_index))
            .filter((value) => Number.isInteger(value))
        );
        if (Number.isInteger(currentBit)) {
          used.delete(Number(currentBit));
        }
        const bits = [];
        for (let bit = 0; bit < SSR_MAX_ENTRIES; bit += 1) {
        if (!used.has(bit)) {
          bits.push(bit);
        }
      }
      if (!bits.length && Number.isInteger(currentBit)) {
        bits.push(Number(currentBit));
      }
        return bits;
      }

      async function updateGroupSsrAssignments(groupId, desiredSsrIds) {
        if (!Array.isArray(desiredSsrIds) || !groupId) {
          return;
        }
        const desiredSet = new Set(desiredSsrIds);
        const currentIds = state.ssrEntries.filter((entry) => entry.group_id === groupId).map((entry) => entry.id);
        const currentSet = new Set(currentIds);

        const updates = [];
        currentIds.forEach((ssrId) => {
          if (!desiredSet.has(ssrId)) {
            updates.push({ id: ssrId, group_id: null });
          }
        });
        desiredSet.forEach((ssrId) => {
          if (!currentSet.has(ssrId)) {
            updates.push({ id: ssrId, group_id: groupId });
          }
        });

        for (const update of updates) {
          await upsertSsrEntry(state.entryId, update);
        }
      }

      function renderPatchPanel() {
        if (!patchPanelPortsContainer || !patchPanelDetails) {
          return;
        }
        hidePatchPanelTooltip();
        if (!state.patchPanelPorts.length || !state.entryId) {
          patchPanelPortsContainer.innerHTML = "";
          patchPanelDetails.innerHTML = '<p class="hint">Select an integration entry to view the patch panel.</p>';
          return;
        }

        const sortedPorts = [...state.patchPanelPorts].sort(
          (a, b) => Number(a.port_number) - Number(b.port_number)
        );
        if (!state.selectedPatchPort && sortedPorts.length) {
          state.selectedPatchPort = Number(sortedPorts[0].port_number);
        }

        renderPatchPanelPortGrid();
        renderPatchPanelDetails();
      }

      function renderPatchPanelPortGrid() {
        if (!patchPanelPortsContainer) {
          return;
        }
        const groupsHtml = PATCH_PANEL_GROUP_RANGES.map(({ start, end, row }) =>
          renderPatchPanelPortGroup(start, end, row)
        ).join("");
        patchPanelPortsContainer.innerHTML = groupsHtml;
      }

      function renderPatchPanelPortGroup(start, end, rowIndex) {
        const tiles = [];
        for (let port = start; port <= end; port += 1) {
          tiles.push(renderPatchPanelPortTile(port));
        }
        const rowClass = rowIndex === 0 ? "top" : "bottom";
        return `<div class="patch-panel-port-block patch-panel-port-block--${rowClass}" data-start="${start}" data-end="${end}">
          <div class="patch-panel-port-strip">${tiles.join("")}</div>
        </div>`;
      }

      function renderPatchPanelPortTile(portNumber) {
        const port = getPatchPanelPortByNumber(portNumber) || { port_number: portNumber, led_ids: [] };
        const classes = ["patch-panel-port-tile"];
        if (Number(state.selectedPatchPort) === portNumber) {
          classes.push("selected");
        }
        if ((port.led_ids || []).length) {
          classes.push("has-links");
        }
        const label = port.label || `Port ${portNumber}`;
        return `<div class="${classes.join(" ")}" tabindex="0" role="button" data-port="${portNumber}" aria-label="${escapeHtml(
          label
        )}">
          <img src="singlePort.svg" alt="" />
        </div>`;
      }

      function selectPatchPanelPort(portNumber) {
        if (!Number.isInteger(portNumber)) {
          return;
        }
        if (state.selectedPatchPort === portNumber) {
          return;
        }
        state.selectedPatchPort = portNumber;
        hidePatchPanelTooltip();
        clearError();
        renderPatchPanel();
      }

      function buildPatchPanelSummary(port) {
        const lines = [];
        lines.push(port.label || `Port ${port.port_number}`);
        const leds = (port.led_ids || []).map((id) => getLedDisplayName(id));
        lines.push(`LEDs: ${leds.length ? leds.join(", ") : "None"}`);
        if (port.notes) {
          lines.push(port.notes);
        }
        return lines.join("\n");
      }

      function getPatchPanelPortByNumber(portNumber) {
        const normalized = Number(portNumber);
        return state.patchPanelPorts.find((entry) => Number(entry.port_number) === normalized);
      }

      function handlePatchPanelPortClick(event) {
        const tile = event.target.closest("[data-port]");
        if (!tile) {
          return;
        }
        const portNumber = Number(tile.dataset.port);
        if (!Number.isInteger(portNumber)) {
          return;
        }
        selectPatchPanelPort(portNumber);
      }

      function handlePatchPanelPortPointerEnter(event) {
        const tile = event.target.closest("[data-port]");
        if (!tile) {
          return;
        }
        const summary = getPatchPanelSummaryForTile(tile);
        showPatchPanelTooltip(event, summary);
      }

      function handlePatchPanelPortPointerMove(event) {
        if (!event.target.closest("[data-port]")) {
          return;
        }
        positionPatchPanelTooltip(event);
      }

      function handlePatchPanelPortPointerLeave(event) {
        if (!patchPanelPortsContainer) {
          hidePatchPanelTooltip();
          return;
        }
        const related = event.relatedTarget;
        if (!related || !patchPanelPortsContainer.contains(related)) {
          hidePatchPanelTooltip();
        }
      }

      function handlePatchPanelPortFocus(event) {
        const tile = event.target.closest("[data-port]");
        if (!tile) {
          return;
        }
        const summary = getPatchPanelSummaryForTile(tile);
        showPatchPanelTooltip(event, summary);
      }

      function handlePatchPanelPortBlur(event) {
        if (!event.target.closest("[data-port]")) {
          return;
        }
        hidePatchPanelTooltip();
      }

      function getPatchPanelSummaryForTile(tile) {
        const portNumber = Number(tile.dataset.port);
        const port =
          getPatchPanelPortByNumber(portNumber) || {
            port_number: portNumber,
            led_ids: [],
          };
        return buildPatchPanelSummary(port);
      }

      function showPatchPanelTooltip(event, summary) {
        if (!patchPanelTooltip || !patchPanelVisual) {
          return;
        }
        if (!summary) {
          hidePatchPanelTooltip();
          return;
        }
        patchPanelTooltip.innerHTML = escapeHtml(summary).replace(/\n/g, "<br />");
        patchPanelTooltip.hidden = false;
        positionPatchPanelTooltip(event);
      }

      function positionPatchPanelTooltip(event) {
        if (!patchPanelTooltip || patchPanelTooltip.hidden || !patchPanelVisual) {
          return;
        }
        const visualRect = patchPanelVisual.getBoundingClientRect();
        let clientX;
        let clientY;
        if (event?.clientX != null && event?.clientY != null) {
          clientX = event.clientX;
          clientY = event.clientY;
        } else if (event?.target?.getBoundingClientRect) {
          const targetRect = event.target.getBoundingClientRect();
          clientX = targetRect.left + targetRect.width / 2;
          clientY = targetRect.top + targetRect.height / 2;
        } else {
          clientX = visualRect.left + visualRect.width / 2;
          clientY = visualRect.top + visualRect.height / 2;
        }
        const offsetX = clientX - visualRect.left;
        const offsetY = clientY - visualRect.top - 16;
        patchPanelTooltip.style.left = `${offsetX}px`;
        patchPanelTooltip.style.top = `${offsetY}px`;
      }

      function hidePatchPanelTooltip() {
        if (patchPanelTooltip) {
          patchPanelTooltip.hidden = true;
        }
      }

      function renderPatchPanelDetails() {
        if (!patchPanelDetails) {
          return;
        }
        const port =
          state.patchPanelPorts.find((entry) => Number(entry.port_number) === Number(state.selectedPatchPort)) ||
          state.patchPanelPorts[0];
        if (!port) {
          patchPanelDetails.innerHTML = '<p class="hint">No patch panel metadata available.</p>';
          return;
        }
        state.selectedPatchPort = Number(port.port_number);
        const ledOptionsHtml = buildLedOptionsHtml(port.led_ids || []);
        const ledNames = (port.led_ids || []).map((id) => getLedDisplayName(id));
        const ledList = ledNames.length
          ? `<ul>${ledNames.map((name) => `<li>${escapeHtml(name)}</li>`).join("")}</ul>`
          : '<p class="hint">No LEDs linked to this port.</p>';
        const selectSize = Math.min(Math.max(state.ledOutputs.length || 4, 4), 12);
        patchPanelDetails.innerHTML = `
          <div class="patch-panel-card">
            <h3>Port ${port.port_number}</h3>
            <form data-patch-port-form data-port="${port.port_number}">
              <label>
                Label
                <input data-field="patch_label" value="${escapeHtml(port.label || "")}" />
              </label>
              <label>
                Notes
                <textarea data-field="patch_notes" rows="3">${escapeHtml(port.notes || "")}</textarea>
              </label>
              <label>
                Linked LEDs
                <select data-field="patch_led_ids" multiple size="${selectSize}">
                  ${ledOptionsHtml}
                </select>
              </label>
              <div class="patch-panel-led-list">
                ${ledList}
              </div>
              <div class="inline">
                <button type="submit" class="primary">Save</button>
                <button type="button" class="secondary" data-action="reset-patch-port">Reset</button>
              </div>
            </form>
          </div>
        `;
      }

      function handlePatchPanelDetailsSubmit(event) {
        const form = event.target.closest("form[data-patch-port-form]");
        if (!form) {
          return;
        }
        event.preventDefault();
        if (!state.entryId) {
          setError("Select an integration entry before updating patch panel details.");
          return;
        }
        withErrorNotice(async () => {
          const payload = readPatchPanelForm(form);
          await upsertPatchPanelPort(state.entryId, payload);
          await refreshAll({ showStatus: false, force: true });
        });
      }

      function handlePatchPanelDetailsClick(event) {
        const button = event.target.closest('button[data-action="reset-patch-port"]');
        if (!button) {
          return;
        }
        event.preventDefault();
        renderPatchPanelDetails();
      }

      function readPatchPanelForm(form) {
        const portNumber = Number(form.dataset.port);
        if (!Number.isInteger(portNumber)) {
          throw new Error("Invalid patch panel port");
        }
        const labelInput = form.querySelector('[data-field="patch_label"]');
        const notesInput = form.querySelector('[data-field="patch_notes"]');
        const ledSelect = form.querySelector('[data-field="patch_led_ids"]');
        const ledIds = ledSelect
          ? Array.from(ledSelect.selectedOptions)
              .map((option) => option.value)
              .filter(Boolean)
          : [];
        return {
          port_number: portNumber,
          label: labelInput?.value.trim() || "",
          notes: notesInput?.value.trim() || "",
          led_ids: ledIds,
        };
      }

      function handleControllerClick(event) {
        const button = event.target.closest("button[data-action]");
        if (!button) {
          return;
        }
        const { action, key } = button.dataset;
        const isDraft = button.dataset.draft === "true";

        if (action === "edit-controller") {
          state.editing.controllers.add(key);
          renderControllers();
          return;
        }

        if (action === "cancel-controller") {
          state.editing.controllers.delete(key);
          if (isDraft) {
            removeDraft("controllers", key);
          }
          renderControllers();
          return;
        }

        if (action === "delete-controller") {
          if (isDraft) {
            removeDraft("controllers", key);
            state.editing.controllers.delete(key);
            renderControllers();
            return;
          }
          if (!confirmDeletion("Delete this controller?")) {
            return;
          }
          withErrorNotice(async () => {
            state.editing.controllers.delete(key);
            await deleteRegistryItem("controllers", key);
          });
          return;
        }

        if (action === "save-controller") {
          const card = button.closest(".card");
          if (!card) {
            return;
          }
          withErrorNotice(async () => {
            const payload = readControllerCard(card);
            state.editing.controllers.delete(key);
            if (isDraft) {
              removeDraft("controllers", key);
            }
            await upsertRegistryItem("controllers", payload);
          });
        }
      }

      async function handleControllerToggle(event) {
        const input = event.target.closest('input[data-action]');
        if (!input) {
          return;
        }
        const action = input.dataset.action;
        if (action !== "toggle-poll" && action !== "toggle-can") {
          return;
        }
        const controllerId = input.dataset.controllerId;
        if (!controllerId) {
          return;
        }
        const enabled = input.checked;
        input.disabled = true;
        try {
          if (action === "toggle-poll") {
            await setControllerPolling(state.entryId, controllerId, enabled);
          } else if (action === "toggle-can") {
            await setControllerCanInterface(state.entryId, controllerId, enabled);
          } else {
            return;
          }
          await refreshAll({ showStatus: false, force: true });
        } catch (error) {
          console.error(error);
          setError(error.message || "Failed to update controller");
          statusEl.textContent = "";
          input.checked = !enabled;
        } finally {
          input.disabled = false;
        }
      }

      function handleDriverClick(event) {
        const button = event.target.closest("button[data-action]");
        if (!button) {
          return;
        }
        const { action, key } = button.dataset;
        const isDraft = button.dataset.draft === "true";

        if (action === "edit-driver") {
          state.editing.drivers.add(key);
          renderDrivers();
          return;
        }

        if (action === "cancel-driver") {
          state.editing.drivers.delete(key);
          if (isDraft) {
            removeDraft("drivers", key);
          }
          renderDrivers();
          return;
        }

        if (action === "delete-driver") {
          if (isDraft) {
            removeDraft("drivers", key);
            state.editing.drivers.delete(key);
            renderDrivers();
            return;
          }
          if (!confirmDeletion("Delete this driver and all of its LED settings?")) {
            return;
          }
          withErrorNotice(async () => {
            state.editing.drivers.delete(key);
            await deleteRegistryItem("drivers", key);
          });
          return;
        }

        if (action === "save-driver") {
          const card = button.closest(".card");
          if (!card) {
            return;
          }
          const originalDriver = findDriverByKey(key);
          withErrorNotice(async () => {
            const payload = readDriverCard(card, key, isDraft);
            const ledConfigUpdates = collectLedConfigChanges(originalDriver, payload);
            state.editing.drivers.delete(key);
            if (isDraft) {
              removeDraft("drivers", key);
            }
            await upsertRegistryItem("drivers", payload);
            if (ledConfigUpdates.length && state.entryId) {
              await sendLedConfigs(state.entryId, ledConfigUpdates);
            }
          });
        }
      }

      function handleDriverInput(event) {
        const target = event.target;
        if (!target) {
          return;
        }
        if (target.dataset.outputField === "name") {
          refreshLedNameValidation();
        }
        if (target.dataset.outputField === "disabled") {
          const card = target.closest(".driver-output-card");
          if (card) {
            card.classList.toggle("disabled", target.checked);
            card.dataset.outputDisabled = target.checked ? "true" : "false";
            if (target.checked) {
              const minInput = card.querySelector('[data-output-field="min_pwm"]');
              const minValue = Number(minInput?.value) || 0;
              card.dataset.outputFaulty = "false";
              card.dataset.outputLevel = "0";
              card.dataset.outputPwm = String(minValue);
              const currentOn = card.querySelector('[data-display="current-on"]');
              if (currentOn) {
                currentOn.checked = false;
              }
              const currentFault = card.querySelector('[data-display="current-fault"]');
              if (currentFault) {
                currentFault.checked = false;
              }
              const currentPwmField = card.querySelector('[data-display="current-pwm"]');
              if (currentPwmField) {
                currentPwmField.value = minValue;
              }
            } else {
              const currentOn = card.querySelector('[data-display="current-on"]');
              if (currentOn) {
                currentOn.checked = Number(card.dataset.outputLevel || 0) > 0;
              }
              const currentFault = card.querySelector('[data-display="current-fault"]');
              if (currentFault) {
                currentFault.checked = card.dataset.outputFaulty === "true";
              }
              const currentPwmField = card.querySelector('[data-display="current-pwm"]');
              if (currentPwmField) {
                currentPwmField.value = Number(card.dataset.outputPwm || 0);
              }
            }
          }
        }
      }

      function renderButtonRow(button, entry, switchKey, editable) {
        const key = getItemKey(button);
        const isDraft = Boolean(button.__tempId);
        if (editable && state.editing.buttons.has(key)) {
          return renderButtonEditorRow(button, entry, switchKey, isDraft);
        }
        return renderButtonSummaryRow(button, entry, switchKey, isDraft);
      }

      function renderButtonSummaryRow(button, entry, switchKey, isDraft) {
        const key = getItemKey(button);
        const groupName = getGroupDisplayName(button.group_id);
        const group = state.groups.find((item) => item.id === button.group_id);
        const isOn = group ? Boolean(group.is_on) : false;
        const toggleDisabled = !button.group_id || !button.id;
        return `
          <tr data-button-row data-key="${key}" data-editing="false">
            <td>${button.name || buildButtonFallbackName(entry, button)}</td>
            <td>${getButtonMaskLabel(button.mask)}</td>
            <td>${groupName}</td>
            <td>
              <label class="toggle-switch${toggleDisabled ? " disabled" : ""}">
                <input type="checkbox" class="button-toggle" data-action="toggle-button" data-button-id="${button.id || ""}" data-group-id="${button.group_id || ""}" ${
                  isOn ? "checked" : ""
                } ${toggleDisabled ? "disabled" : ""} />
                <span class="toggle-slider"></span>
              </label>
            </td>
            ${
              state.editing.switches.has(switchKey)
                ? `<td class="button-row-actions"><button class="secondary" data-action="edit-button" data-key="${key}" data-switch-key="${switchKey}" data-draft="${isDraft}">Edit</button></td>`
                : ""
            }
          </tr>
        `;
      }

      function renderButtonEditorRow(button, entry, switchKey, isDraft) {
        const key = getItemKey(button);
        const metadataJson = escapeHtml(JSON.stringify(button.metadata || {}));
        const nameValue = button.name || "";
        const groupValue = button.group_id || "";
        const maskValue = Number(button.mask) || 1;
        const takenMasks = getTakenMasksForSwitch(entry, key);
        const discardButton = isDraft
          ? ""
          : `<button class="secondary" data-action="discard-button" data-key="${key}" data-switch-key="${switchKey}" data-draft="${isDraft}">Revert</button>`;
        const deleteLabel = isDraft ? "Discard" : "Delete";

        return `
          <tr class="button-editor" data-button-row data-key="${key}" data-editing="true">
            <td colspan="5">
              <div class="stack" style="gap: 0.75rem;" data-id="${button.id || ""}" data-draft="${isDraft}" data-switch-key="${switchKey}" data-switch-id="${entry.id || ""}" data-metadata="${metadataJson}">
                <div class="two-column">
                  <label>
                    Name
                    <input type="text" data-field="name" value="${nameValue}" placeholder="Button name" />
                  </label>
                  <label>
                    Mask
                    <select data-field="mask">
                      ${buildButtonMaskOptions(maskValue, takenMasks)}
                    </select>
                  </label>
                  <label>
                    Group
                    <select data-field="group_id">
                      ${buildGroupOptions(groupValue)}
                    </select>
                  </label>
                </div>
                <div class="button-row-actions">
                  ${discardButton}
                  <button class="danger" data-action="delete-button" data-key="${key}" data-switch-key="${switchKey}" data-draft="${isDraft}">${deleteLabel}</button>
                </div>
              </div>
            </td>
          </tr>
        `;
      }

      function getButtonsForSwitch(entry) {
        const key = getItemKey(entry);
        const result = [];
        const switchId = entry.id;
        const switchValue = safeNumericSwitch(entry.switch);

        state.buttons.forEach((button) => {
          if (switchId && button.switch_id === switchId) {
            result.push(button);
            return;
          }
          if (!switchId && Number.isFinite(switchValue) && Number(button.switch) === switchValue) {
            result.push(button);
          }
        });

        state.drafts.buttons.forEach((button) => {
          if (button.__parentKey && button.__parentKey === key) {
            result.push(button);
            return;
          }
          if (switchId && button.switch_id === switchId) {
            result.push(button);
          }
        });

        return result;
      }

      function getTakenMasksForSwitch(entry, excludeKey) {
        const taken = new Set();
        getButtonsForSwitch(entry).forEach((button) => {
          if (getItemKey(button) === excludeKey) {
            return;
          }
          const value = Number(button.mask);
          if (Number.isFinite(value)) {
            taken.add(value);
          }
        });
        return taken;
      }

      const SWITCH_TYPE_OPTIONS = [
        { value: "momentary", label: "Momentary" },
        { value: "toggle", label: "Toggle" },
        { value: "dimmer", label: "Dimmer" },
      ];

      function buildSwitchTypeOptions(selected) {
        const value = selected || "momentary";
        return SWITCH_TYPE_OPTIONS.map((option) => {
          const isSelected = option.value === value;
          return `<option value="${option.value}"${isSelected ? " selected" : ""}>${option.label}</option>`;
        }).join("");
      }

      function buildButtonFallbackName(entry, button) {
        const switchValue = entry.switch ?? button.switch ?? "";
        const index = getButtonIndexFromMask(button.mask);
        if (Number.isFinite(Number(switchValue))) {
          return index ? `Switch ${switchValue} Button ${index}` : `Switch ${switchValue} Button`;
        }
        return index ? `Button ${index}` : "Button";
      }

      function buildButtonMaskOptions(selectedMask, takenMasks = new Set()) {
        const selectedValue = Number(selectedMask);
        return SWITCH_MASK_CHOICES.map((choice) => {
          const disabled = takenMasks.has(choice.value) && choice.value !== selectedValue;
          return `<option value="${choice.value}"${choice.value === selectedValue ? " selected" : ""}${disabled ? " disabled" : ""}>${choice.label}</option>`;
        }).join("");
      }

      function handleSwitchClick(event) {
        const button = event.target.closest("button[data-action]");
        if (!button) {
          return;
        }
        const { action } = button.dataset;
        if (!action) {
          return;
        }

        if (action === "save-buttons") {
          const switchKey = button.dataset.switchKey;
          const parent = findSwitchByKey(switchKey);
          if (!parent || !parent.id) {
            setError("Save the switch before adding buttons.");
            return;
          }
          const card = button.closest(".switch-card");
          if (!card) {
            return;
          }
          const editorRows = Array.from(card.querySelectorAll('[data-button-row][data-editing="true"]'));
          if (!editorRows.length) {
            clearError();
            return;
          }
          withErrorNotice(async () => {
            const updates = editorRows.map((row) => {
              const payload = readButtonCard(row);
              const meta = row.querySelector("[data-draft]");
              return {
                payload,
                key: row.dataset.key,
                isDraft: meta?.dataset.draft === "true",
              };
            });
            for (const update of updates) {
              await upsertRegistryItem("buttons", update.payload, { refresh: false });
              state.editing.buttons.delete(update.key);
              if (update.isDraft) {
                removeDraft("buttons", update.key);
              }
            }
            pendingSwitchSelectKey = parent.id || getItemKey(parent);
            await refreshAll({ showStatus: false, force: true });
          });
          return;
        }

        if (action === "cancel-buttons") {
          const switchKey = button.dataset.switchKey;
          const parent = findSwitchByKey(switchKey);
          if (!parent) {
            return;
          }
          const card = button.closest(".switch-card");
          if (!card) {
            return;
          }
          const editorRows = Array.from(card.querySelectorAll('[data-button-row][data-editing="true"]'));
          editorRows.forEach((row) => {
            const key = row.dataset.key;
            const meta = row.querySelector("[data-draft]");
            const isDraft = meta?.dataset.draft === "true";
            state.editing.buttons.delete(key);
            if (isDraft) {
              removeDraft("buttons", key);
            }
          });
          renderSwitches();
          clearError();
          return;
        }

        if (action === "add-button") {
          const switchKey = button.dataset.switchKey;
          const parent = findSwitchByKey(switchKey);
          if (!parent) {
            return;
          }
          if (!parent.id) {
            setError("Save the switch before adding buttons.");
            return;
          }
          startButtonDraft(switchKey, { switch_id: parent.id, switch: parent.switch });
          return;
        }

        if (action.endsWith("-button")) {
          handleButtonAction(action, button);
          return;
        }

        const key = button.dataset.key;
        const isDraft = button.dataset.draft === "true";

        if (action === "edit-switch") {
          state.editing.switches.add(key);
          renderSwitches();
          return;
        }

        if (action === "cancel-switch") {
          state.editing.switches.delete(key);
          if (isDraft) {
            removeDraft("switches", key);
          }
          renderSwitches();
          return;
        }

        if (action === "delete-switch") {
          if (isDraft) {
            removeDraft("switches", key);
            state.editing.switches.delete(key);
            renderSwitches();
            return;
          }
          if (!confirmDeletion("Delete this switch and its buttons?")) {
            return;
          }
          withErrorNotice(async () => {
            state.editing.switches.delete(key);
            await deleteRegistryItem("switches", key);
          });
          return;
        }

        if (action === "save-switch") {
          const card = button.closest(".switch-card.editor");
          if (!card) {
            return;
          }
        withErrorNotice(async () => {
          const payload = readSwitchCard(card);
          state.editing.switches.delete(key);
          if (isDraft) {
            removeDraft("switches", key);
          }
          const stored = await upsertRegistryItem("switches", payload);
          if (stored?.id) {
            pendingSwitchSelectKey = stored.id;
          }
        });
          return;
        }

      }

function handleButtonAction(action, element) {
  const key = element.dataset.key;
  const isDraft = element.dataset.draft === "true";
  const switchKey = element.dataset.switchKey;

  if (action === "edit-button") {
    state.editing.buttons.add(key);
    renderSwitches();
    return;
  }

  if (action === "delete-button") {
    if (isDraft) {
      removeDraft("buttons", key);
      state.editing.buttons.delete(key);
      renderSwitches();
      return;
    }
    if (!confirmDeletion("Delete this button mapping?")) {
      return;
    }
    withErrorNotice(async () => {
      state.editing.buttons.delete(key);
      await deleteRegistryItem("buttons", key);
    });
    return;
  }

  if (action === "discard-button") {
    state.editing.buttons.delete(key);
    if (isDraft) {
      removeDraft("buttons", key);
    }
    renderSwitches();
  }
}

function disableButtonToggle(buttonId) {
  const existing = pendingButtonToggles.get(buttonId);
  if (existing?.timer) {
    clearTimeout(existing.timer);
  }
  const timeout = setTimeout(() => {
    pendingButtonToggles.delete(buttonId);
    const input = document.querySelector(`input[data-action="toggle-button"][data-button-id="${buttonId}"]`);
    if (input) {
      input.disabled = false;
      input.closest(".toggle-switch")?.classList.remove("disabled");
    }
  }, 5000);
  pendingButtonToggles.set(buttonId, { timer: timeout });
  const input = document.querySelector(`input[data-action="toggle-button"][data-button-id="${buttonId}"]`);
  if (input) {
    input.disabled = true;
    input.closest(".toggle-switch")?.classList.add("disabled");
  }
}

function clearButtonToggle(buttonId) {
  const entry = pendingButtonToggles.get(buttonId);
  if (entry?.timer) {
    clearTimeout(entry.timer);
  }
  pendingButtonToggles.delete(buttonId);
  const input = document.querySelector(`input[data-action="toggle-button"][data-button-id="${buttonId}"]`);
  if (input) {
    input.disabled = false;
    input.closest(".toggle-switch")?.classList.remove("disabled");
  }
}

function handleButtonToggle(event) {
  const toggle = event.target.closest('input[data-action="toggle-button"]');
  if (!toggle) {
    return;
  }
  if (isEditing()) {
    event.preventDefault();
    event.target.checked = !event.target.checked;
    return;
  }
  const groupId = toggle.dataset.groupId;
  if (!groupId) {
    setError("Assign this button to a group before toggling it.");
    toggle.checked = !toggle.checked;
    return;
  }
  const buttonId = toggle.dataset.buttonId;
  if (buttonId) {
    disableButtonToggle(buttonId);
  }
  withErrorNotice(async () => {
    await sendGroupCommand(state.entryId, groupId, toggle.checked);
    await refreshAll({ showStatus: false, force: true });
  });
}

function previewGroupBrightness(groupId, percent) {
  const targets = buildGroupLedTargets(groupId, percent);
  applyLedPreview(targets);
  return targets;
}

function buildGroupLedTargets(groupId, percent) {
  const group = state.groups.find((entry) => entry.id === groupId);
  if (!group) {
    return {};
  }
  const clampedPercent = Math.min(Math.max(Number(percent) || 0, 0), 100);
  const ledIds = group.led_ids || [];
  const targets = {};
  ledIds.forEach((ledId) => {
    const output = state.ledOutputs.find((item) => item.id === ledId);
    if (!output || (output.disabled && !output.faulty)) {
      return;
    }
    targets[ledId] = computeLedPwmForOutput(output, clampedPercent);
  });
  return targets;
}

function applyLedPreview(targets = {}) {
  Object.entries(targets).forEach(([ledId, pwm]) => {
    state.pendingLedPwm[ledId] = pwm;
    const descriptor = state.ledOutputs.find((item) => item.id === ledId);
    if (descriptor) {
      descriptor.target_pwm = pwm;
    }
    updateLedCardPreview(ledId, pwm);
  });
  cleanPendingLedPwm();
}

function updateLedCardPreview(ledId, pwm) {
  if (!ledId) {
    return;
  }
  const selector = `.driver-output-card[data-output-id="${cssEscapeId(ledId)}"]`;
  const card = document.querySelector(selector);
  if (!card) {
    return;
  }
  card.dataset.outputTargetPwm = String(pwm);
  const targetField = card.querySelector('[data-display="target-pwm"]');
  if (targetField) {
    targetField.value = pwm;
  }
}

function cssEscapeId(value) {
  if (window.CSS?.escape) {
    return window.CSS.escape(value);
  }
  return String(value).replace(/"/g, '\\"');
}

function computeLedPwmForOutput(output, percent) {
  const min = Number.isFinite(Number(output.min_pwm)) ? Number(output.min_pwm) : 0;
  const rawMax = Number(output.max_pwm);
  const max = Number.isFinite(rawMax) ? rawMax : 255;
  const normalized = Math.min(Math.max(percent, 0), 100) / 100;
  const span = Math.max(max - min, 0);
  return Math.round(min + span * normalized);
}

function cleanPendingLedPwm() {
  const known = new Set(state.ledOutputs.map((output) => output.id));
  Object.keys(state.pendingLedPwm).forEach((ledId) => {
    if (!known.has(ledId)) {
      delete state.pendingLedPwm[ledId];
    }
  });
}

function handleGroupSliderInput(event) {
  const slider = event.target.closest('input[data-action="group-slider"]');
  if (!slider) {
    return;
  }
  const value = Number(slider.value) || 0;
  const label = slider.closest(".group-slider")?.querySelector(".group-slider-value");
  if (label) {
    label.textContent = `${value}%`;
  }
  const groupId = slider.dataset.groupId;
  if (groupId) {
    previewGroupBrightness(groupId, value);
  }
}

function handleGroupSliderChange(event) {
  const slider = event.target.closest('input[data-action="group-slider"]');
  if (!slider) {
    return;
  }
  const groupId = slider.dataset.groupId;
  if (!groupId) {
    return;
  }
  const group = state.groups.find((entry) => entry.id === groupId);
  if (!group) {
    return;
  }
  const value = Number(slider.value) || 0;
  slider.disabled = true;
  const label = slider.closest(".group-slider")?.querySelector(".group-slider-value");
  if (label) {
    label.textContent = `${value}%`;
  }
  const targets = previewGroupBrightness(groupId, value);
  console.debug("Group slider change", {
    groupId,
    value,
    targets,
    entryId: state.entryId,
  });

  withErrorNotice(async () => {
    const payload = {
      id: group.id,
      name: group.name,
      led_ids: group.led_ids || [],
      is_on: group.is_on,
      brightness: value,
    };
    await upsertRegistryItem("groups", payload, { refresh: false });
    const targetKeys = Object.keys(targets || {});
    if (targetKeys.length) {
      await Promise.all([
        setLedOutputTargets(state.entryId, targets),
        sendGroupPwmTargets(state.entryId, groupId, targets),
      ]);
      console.debug("Dispatched PWM updates", { groupId, targetCount: targetKeys.length });
    }
    await refreshAll({ showStatus: false, force: true });
  }).finally(() => {
    slider.disabled = false;
  });
}

function handleGroupSliderPointerDown(event) {
  const slider = event.target.closest('input[data-action="group-slider"]');
  if (!slider) {
    return;
  }
  state.activeGroupSlider = slider.dataset.groupId || "__any";
}

function handleGroupSliderPointerUp() {
  if (!state.activeGroupSlider) {
    return;
  }
  state.activeGroupSlider = null;
  renderGroups();
}

function findDriverByKey(key) {
  const all = [...state.drivers, ...state.drafts.drivers];
  return all.find((entry) => getItemKey(entry) === key) || null;
}

function collectLedConfigChanges(originalDriver, updatedPayload) {
  const controllerId = updatedPayload.controller_id || originalDriver?.controller_id;
  const driverIndexRaw =
    updatedPayload.driver_index ?? originalDriver?.driver_index ?? updatedPayload.metadata?.driver_index;
  const driverIndex = Number(driverIndexRaw);
  if (!controllerId || !Number.isFinite(driverIndex)) {
    return [];
  }
  const originalOutputs = ensureDriverOutputsClone(originalDriver || { outputs: [] });
  const updatedOutputs = ensureDriverOutputsClone({ outputs: updatedPayload.outputs || [] });
  const changes = [];
  updatedOutputs.forEach((output, index) => {
    const original = originalOutputs[index];
    const changed =
      !original ||
      Number(original.min_pwm ?? 0) !== Number(output.min_pwm ?? 0) ||
      Number(original.max_pwm ?? 255) !== Number(output.max_pwm ?? 255) ||
      Boolean(original.disabled) !== Boolean(output.disabled) ||
      !areChannelsEqual(original.channels, output.channels);
    if (!changed) {
      return;
    }
    const channel = getPrimaryChannel(output);
    if (channel === null) {
      return;
    }
    changes.push({
      controller_id: controllerId,
      driver_index: driverIndex,
      channel,
      min_pwm: Number(output.min_pwm ?? 0),
      max_pwm: Number(output.max_pwm ?? 255),
      current_high: Boolean(output.current_high ?? output.metadata?.current_high),
    });
  });
  return changes;
}

function areChannelsEqual(left = [], right = []) {
  const leftList = Array.isArray(left) ? left.map(Number).filter(Number.isFinite) : [];
  const rightList = Array.isArray(right) ? right.map(Number).filter(Number.isFinite) : [];
  if (leftList.length !== rightList.length) {
    return false;
  }
  for (let idx = 0; idx < leftList.length; idx += 1) {
    if (leftList[idx] !== rightList[idx]) {
      return false;
    }
  }
  return true;
}

function getPrimaryChannel(output) {
  if (!output) {
    return null;
  }
  if (Array.isArray(output.channels) && output.channels.length) {
    const candidate = Number(output.channels[0]);
    if (Number.isFinite(candidate)) {
      return candidate;
    }
  }
  const slot = Number(output.slot);
  return Number.isFinite(slot) ? slot : null;
}
function findSwitchByKey(key) {
  const all = [...state.switches, ...state.drafts.switches];
  return all.find((entry) => getItemKey(entry) === key) || null;
}

      function startSwitchDraft(initial = {}) {
        const metadata = { ...(initial.metadata || {}) };
        const draft = {
          id: "",
          name: initial.name || "",
          switch: initial.switch ?? "",
          type: initial.type || "momentary",
          button_count: Number(initial.button_count ?? 5),
          has_buzzer: Boolean(initial.has_buzzer || false),
          flash_leds: initial.flash_leds === false ? false : true,
          metadata,
        };
        addDraft("switches", draft);
        renderAll();
        clearError();
      }

      function startButtonDraft(switchKey, initial = {}) {
        const parent = findSwitchByKey(switchKey);
        if (!parent) {
          setError("Unable to determine switch for new button.");
          return;
        }

        const buttonCount = Number(parent.button_count ?? 5);
        const existingButtons = getButtonsForSwitch(parent);
        if (existingButtons.length >= buttonCount) {
          setError("Configured button limit reached for this switch.");
          return;
        }

        const availableMask = SWITCH_MASK_CHOICES.find((choice) =>
          !existingButtons.some((button) => Number(button.mask) === choice.value)
        );
        const metadata = { ...(initial.metadata || {}) };
        const draft = {
          id: "",
          name: initial.name || "",
          switch_id: initial.switch_id || parent.id || "",
          switch: initial.switch ?? parent.switch ?? "",
          mask: initial.mask || availableMask?.value || 1,
          group_id: initial.group_id || "",
          metadata,
          __parentKey: switchKey,
        };
        const draftKey = addDraft("buttons", draft);
        pendingSwitchSelectKey = getItemKey(parent);
        state.editing.buttons.add(draftKey);
        renderSwitches();
        clearError();
      }

      function renderButtonDiscoveries() {
        if (!buttonDiscoveriesContainer) {
          return;
        }

        const discoveries = (state.learnedButtons || []).slice();
        if (!discoveries.length) {
          buttonDiscoveriesContainer.innerHTML = "";
          buttonDiscoveriesContainer.classList.add("hidden");
          return;
        }

        discoveries.sort((a, b) => {
          const switchA = Number(a.switch);
          const switchB = Number(b.switch);
          if (Number.isFinite(switchA) && Number.isFinite(switchB) && switchA !== switchB) {
            return switchA - switchB;
          }
          const lastA = Number(a.last_seen || 0);
          const lastB = Number(b.last_seen || 0);
          return lastB - lastA;
        });

        const cards = discoveries
          .map((entry) => {
            const switchValue = Number(entry.switch);
            const maskLabel = getButtonMaskLabel(entry.mask);
            const lastSeen = formatTimestamp(entry.last_seen);
            const count = entry.count || 1;
            const controllerLabel = entry.controller_id ? ` · Controller ${entry.controller_id}` : "";
            const switchName = Number.isFinite(switchValue) ? `Switch ${switchValue}` : "Unknown switch";
            const existingSwitch = state.switches.find((item) => Number(item.switch) === switchValue);
            const action = existingSwitch ? "adopt-button" : "adopt-switch";
            const actionLabel = existingSwitch ? "Add Button" : "Add Switch";

            return `
              <div class="switch-discovery-card" data-key="${entry.id}">
                <header>
                  <span>${switchName}</span>
                  <span>${maskLabel}</span>
                </header>
                <p class="switch-discovery-meta">
                  Last seen: ${lastSeen}${controllerLabel} · Presses: ${count}
                </p>
                <div class="switch-discovery-actions">
                  <button class="secondary" data-action="${action}" data-key="${entry.id}">${actionLabel}</button>
                  <button class="warn" data-action="delete-discovery" data-key="${entry.id}">Delete</button>
                </div>
              </div>
            `;
          })
          .join("");

        buttonDiscoveriesContainer.innerHTML = `<p class="hint">Recently discovered button activity</p>${cards}`;
        buttonDiscoveriesContainer.classList.remove("hidden");
      }

      function handleButtonDiscoveryClick(event) {
        const button = event.target.closest("button[data-action]");
        if (!button) {
          return;
        }
        const { action, key } = button.dataset;
        if (!key) {
          return;
        }
        const entry = (state.learnedButtons || []).find((item) => item.id === key);
        if (!entry) {
          return;
        }
        if (action === "adopt-button") {
          const switchValue = Number(entry.switch);
          const existing = state.switches.find((item) => Number(item.switch) === switchValue);
          if (existing) {
            startButtonDraft(getItemKey(existing), {
              switch_id: existing.id,
              switch: existing.switch,
              mask: Number(entry.mask) || 1,
            });
            clearError();
            return;
          }
          startSwitchDraft({ switch: Number.isFinite(switchValue) ? switchValue : "" });
          clearError();
          return;
        }
        if (action === "adopt-switch") {
          const switchValue = Number(entry.switch);
          startSwitchDraft({ switch: Number.isFinite(switchValue) ? switchValue : "" });
          clearError();
          return;
        }
        if (action === "delete-discovery") {
          if (!state.entryId) {
            setError("Select an integration entry before deleting discoveries.");
            return;
          }
          deleteLearnedButton(state.entryId, entry.id)
            .then(() => refreshAll({ force: true }))
            .catch(setError);
          return;
        }
      }

      function handlePortClick(event) {
        const cell = event.target.closest(".port-cell");
        if (!cell || cell.dataset.disabled === "true") {
          return;
        }
        activatePortCell(cell);
      }

      function handlePortKeydown(event) {
        if (event.key !== "Enter" && event.key !== " ") {
          return;
        }
        const cell = event.target.closest(".port-cell");
        if (!cell || cell.dataset.disabled === "true") {
          return;
        }
        event.preventDefault();
        activatePortCell(cell);
      }

      function activatePortCell(cell) {
        const driverKey = cell.dataset.driverKey;
        if (!driverKey) {
          return;
        }
        state.editing.drivers.add(driverKey);
        renderDrivers();
        setTimeout(() => {
          const target = driversContainer.querySelector(`[data-key="${driverKey}"]`);
          if (target) {
            target.scrollIntoView({ behavior: "smooth", block: "center" });
          }
        }, 0);
      }

      function handleGroupClick(event) {
        const button = event.target.closest("button[data-action]");
        if (!button) {
          return;
        }
        const { action, key } = button.dataset;
        const isDraft = button.dataset.draft === "true";

        if (action === "edit-group") {
          state.editing.groups.add(key);
          renderGroupDefinitions();
          return;
        }

        if (action === "cancel-group") {
          state.editing.groups.delete(key);
          if (isDraft) {
            removeDraft("groups", key);
          }
          renderGroupDefinitions();
          return;
        }

        if (action === "delete-group") {
          if (isDraft) {
            removeDraft("groups", key);
            state.editing.groups.delete(key);
            renderGroupDefinitions();
            return;
          }
          if (!confirmDeletion("Delete this group definition?")) {
            return;
          }
          withErrorNotice(async () => {
            state.editing.groups.delete(key);
            await deleteRegistryItem("groups", key);
          });
          return;
        }

        if (action === "save-group") {
          const card = button.closest(".card");
          if (!card) {
            return;
          }
          withErrorNotice(async () => {
            const payload = readGroupCard(card);
            state.editing.groups.delete(key);
            if (isDraft) {
              removeDraft("groups", key);
            }
            await upsertRegistryItem("groups", payload);
          });
        }
      }

      function startControllerDraft() {
        const draft = {
          id: "",
          name: "",
          port: "",
          baudrate: 115200,
          metadata: {},
          polling_enabled: false,
          has_can_interface: false,
        };
        addDraft("controllers", draft);
        renderAll();
      }

      function startDriverDraft() {
        const controllers = state.controllers;
        const allocateLedName = createLedNameAllocator();
        const draft = {
          id: "",
          name: "",
          controller_id: controllers[0]?.id || "",
          driver_index: 0,
          outputs: Array.from({ length: 4 }, (_, slot) => ({
            id: "",
            slot,
            name: allocateLedName(),
            channels: [slot],
            faulty: false,
            pwm: 0,
            level: 0,
            min_pwm: 0,
            max_pwm: 255,
            disabled: false,
          })),
        };
        addDraft("drivers", draft);
        renderAll();
      }

      function startGroupDraft() {
        const draft = {
          id: "",
          name: "",
          led_ids: [],
          is_on: false,
          brightness: 0,
        };
        addDraft("groups", draft);
        renderAll();
      }

      function getGroupDisplayName(groupId) {
        if (!groupId) {
          return "Unassigned";
        }
        const group = state.groups.find((entry) => entry.id === groupId);
        return group?.name || groupId;
      }

      function buildGroupOptions(selectedId) {
        const options = ['<option value="">Unassigned</option>'];
        state.groups.forEach((group) => {
          const value = group.id || "";
          const name = group.name || group.id || value || "Group";
          const selected = value === selectedId ? " selected" : "";
          options.push(`<option value="${value}"${selected}>${name}</option>`);
        });
        return options.join("");
      }

      function buildLedOptionsHtml(selectedLedIds = [], { includeSsr = false, selectedSsrIds = [] } = {}) {
        const selectedLedSet = new Set(selectedLedIds || []);
        const options = state.ledOutputs
          .map((output) => {
            const id = output.id || "";
            const label = `${getLedDisplayName(output.id)} (${getControllerDisplayName(output.controller_id)} · driver ${
              output.driver_index
            } slot ${output.slot + 1})${output.disabled ? " [disabled]" : ""}`;
            const selected = selectedLedSet.has(id) ? " selected" : "";
            return `<option value="${id}"${selected}>${label}</option>`;
          })
          .join("");

        if (!includeSsr) {
          return options;
        }

        const selectedSsrSet = new Set(selectedSsrIds || []);
        const ssrOptions = state.ssrEntries
          .map((entry) => {
            const value = `ssr:${entry.id}`;
            const label = `${entry.name || entry.id || "SSR"} (Bit ${entry.bit_index})`;
            const selected = selectedSsrSet.has(entry.id) ? " selected" : "";
            return `<option value="${value}"${selected}>${label}</option>`;
          })
          .join("");

        return [options, ssrOptions].filter(Boolean).join("");
      }

      function getGroupSsrIds(groupId) {
        if (!groupId) {
          return [];
        }
        return state.ssrEntries.filter((entry) => entry.group_id === groupId).map((entry) => entry.id);
      }

      function getGroupMemberNames(group) {
        if (!group) {
          return [];
        }
        const fromLedIds = (group.led_ids || []).map((id) => resolveMemberDisplayName(id)).filter(Boolean);
        const fromSsr = state.ssrEntries
          .filter((entry) => entry.group_id === group.id)
          .map((entry) => entry.name || `SSR bit ${entry.bit_index}`);
        return [...fromLedIds, ...fromSsr];
      }

      function resolveMemberDisplayName(memberId) {
        if (!memberId) {
          return "";
        }
        if (memberId.startsWith("ssr:")) {
          const ssrId = memberId.slice(4);
          const entry = state.ssrEntries.find((item) => item.id === ssrId);
          if (entry) {
            return entry.name || `SSR bit ${entry.bit_index}`;
          }
          return memberId;
        }
        return getLedDisplayName(memberId);
      }

      function getButtonMaskLabel(mask) {
        const numeric = Number(mask);
        if (!Number.isFinite(numeric) || numeric <= 0) {
          return "Unassigned";
        }
        const choice = SWITCH_MASK_CHOICES.find((entry) => entry.value === numeric);
        if (choice) {
          return choice.label;
        }
        const index = getButtonIndexFromMask(numeric);
        if (index) {
          return `Button ${index} (mask ${numeric})`;
        }
        return `Mask ${numeric}`;
      }

      function getButtonIndexFromMask(mask) {
        const numeric = Number(mask);
        if (!Number.isFinite(numeric) || numeric <= 0) {
          return null;
        }
        if ((numeric & (numeric - 1)) !== 0) {
          return null;
        }
        return Math.log2(numeric) + 1;
      }

function readControllerCard(card) {
        const payload = {
          id: card.dataset.id || undefined,
          name: card.querySelector('[data-field="name"]').value.trim(),
          port: card.querySelector('[data-field="port"]').value.trim(),
          baudrate: Number(card.querySelector('[data-field="baudrate"]').value) || 115200,
        };
        if (payload.id) {
          const existing = state.controllers.find((ctrl) => ctrl.id === payload.id);
          if (existing && Object.prototype.hasOwnProperty.call(existing, "polling_enabled")) {
            payload.polling_enabled = Boolean(existing.polling_enabled);
          }
          if (existing && Object.prototype.hasOwnProperty.call(existing, "has_can_interface")) {
            payload.has_can_interface = Boolean(existing.has_can_interface);
          }
        }
        const metadataText = card.querySelector('[data-field="metadata"]').value.trim();
        if (metadataText) {
          try {
            payload.metadata = JSON.parse(metadataText);
          } catch (error) {
            throw new Error("Metadata must be valid JSON.");
          }
        }
        const canField = card.querySelector('[data-field="has_can_interface"]');
        if (canField && !canField.disabled) {
          payload.has_can_interface = Boolean(canField.checked);
        }
        return payload;
      }

      function readDriverCard(card, key, isDraft) {
        const driverId = card.dataset.id || undefined;
        const controllerSelect = card.querySelector('[data-field="controller_id"]');
        const driverIndexInput = card.querySelector('[data-field="driver_index"]');
        const nameInput = card.querySelector('[data-field="driver_name"]');

        const payload = {
          id: driverId,
          name: nameInput?.value.trim() || "",
          controller_id: controllerSelect?.value.trim() || "",
          driver_index: Number(driverIndexInput?.value) || 0,
          outputs: [],
        };

        const driverKeyResolved = driverId || key;
        const localLedNames = new Set();

        const outputCards = card.querySelectorAll(".driver-output-card");
        outputCards.forEach((outputCard) => {
          const slot = Number(outputCard.dataset.slot);
          const baseId = outputCard.dataset.outputId || `${key}_slot${slot}`;
          const outputKey = makeOutputKey(driverKeyResolved, baseId, slot);
          const getField = (selector) => outputCard.querySelector(`[data-output-field="${selector}"]`);
          const nameField = getField("name");
          const minField = getField("min_pwm");
          const maxField = getField("max_pwm");
          const disabledField = getField("disabled");

          const channels = (() => {
            const stored = outputCard.dataset.outputChannels || "";
            const parsed = stored
              .split(",")
              .map((value) => value.trim())
              .filter(Boolean)
              .map((value) => Number(value))
              .filter((value) => Number.isInteger(value) && value >= 0);
            if (parsed.length) {
              return parsed;
            }
            return [slot];
          })();
          outputCard.dataset.outputChannels = channels.join(",");

          const nameValue = nameField?.value.trim() || "";
          const normalizedName = normalizeLedName(nameValue);
          if (normalizedName) {
            if (localLedNames.has(normalizedName)) {
              throw new Error("Each LED output on a driver must have a unique name.");
            }
            localLedNames.add(normalizedName);
          }

          const disabled = Boolean(disabledField?.checked);
          const faultyData = outputCard.dataset.outputFaulty === "true";

          payload.outputs.push({
            id: baseId,
            slot,
            name: nameValue,
            channels,
            faulty: disabled ? false : faultyData,
            min_pwm: Number(minField?.value) || 0,
            max_pwm: Number(maxField?.value) || 255,
            target_pwm: Number(outputCard.dataset.outputTargetPwm || outputCard.dataset.outputPwm || 0),
            pwm: Number(outputCard.dataset.outputPwm || 0),
            level: Number(outputCard.dataset.outputLevel || 0),
            disabled,
          });

          if (isLedNameDuplicate(nameValue, outputKey)) {
            throw new Error("LED output names must be unique across all drivers.");
          }
        });

        return payload;
      }

      function readButtonCard(row) {
        const container = row.querySelector("[data-switch-key]");
        if (!container) {
          throw new Error("Unable to locate button editor container.");
        }

        const switchKey = container.dataset.switchKey;
        const parent = findSwitchByKey(switchKey);
        if (!parent || !parent.id) {
          throw new Error("Save the switch before editing its buttons.");
        }

        const nameInput = container.querySelector('[data-field="name"]');
        const maskInput = container.querySelector('[data-field="mask"]');
        const groupInput = container.querySelector('[data-field="group_id"]');

        const maskValue = Number(maskInput?.value);
        if (!Number.isInteger(maskValue) || maskValue <= 0 || (maskValue & (maskValue - 1)) !== 0) {
          throw new Error("Select a valid button position.");
        }

        const nameValue = nameInput?.value.trim() || "";
        const fallbackName = buildButtonFallbackName(parent, { mask: maskValue });

        const switchNumeric = safeNumericSwitch(parent.switch);
        const payload = {
          id: container.dataset.id || undefined,
          name: nameValue || fallbackName,
          switch_id: parent.id,
          switch: Number.isFinite(switchNumeric) ? switchNumeric : 0,
          mask: maskValue,
        };

        const groupId = groupInput?.value || "";
        payload.group_id = groupId ? groupId : null;

        let metadata = {};
        try {
          metadata = JSON.parse(container.dataset.metadata || "{}");
        } catch (error) {
          metadata = {};
        }
        payload.metadata = metadata;
        container.dataset.metadata = JSON.stringify(metadata);

        return payload;
      }

      function readGroupCard(card) {
        const ledSelect = card.querySelector('[data-field="led_ids"]');
        const ledIds = ledSelect
          ? Array.from(ledSelect.selectedOptions).map((option) => option.value).filter(Boolean)
          : [];
        return {
          id: card.dataset.id || undefined,
          name: card.querySelector('[data-field="name"]').value.trim(),
          led_ids: ledIds,
          brightness: Number(card.querySelector('[data-field="brightness"]').value) || 0,
          is_on: card.querySelector('[data-field="is_on"]').value === "true",
        };
      }

      function readSwitchCard(card) {
        const nameInput = card.querySelector('[data-field="name"]');
        const switchInput = card.querySelector('[data-field="switch"]');
        const typeInput = card.querySelector('[data-field="type"]');
        const countInput = card.querySelector('[data-field="button_count"]');
        const buzzerInput = card.querySelector('[data-field="has_buzzer"]');
        const flashInput = card.querySelector('[data-field="flash_leds"]');

        const switchValue = Number(switchInput?.value);
        if (!Number.isInteger(switchValue) || switchValue < 0) {
          throw new Error("Switch id must be a non-negative whole number.");
        }

        let buttonCount = Number(countInput?.value);
        if (!Number.isFinite(buttonCount)) {
          buttonCount = 5;
        }
        buttonCount = Math.round(buttonCount);
        if (buttonCount < 1 || buttonCount > 5) {
          throw new Error("Button count must be between 1 and 5.");
        }

        const key = card.dataset.key;
        const existingConflict = [...state.switches, ...state.drafts.switches].find((entry) => {
          if (getItemKey(entry) === key) {
            return false;
          }
          return Number(entry.switch) === switchValue;
        });
        if (existingConflict) {
          throw new Error(`Switch ${switchValue} is already configured.`);
        }

        const parent = key ? findSwitchByKey(key) : null;
        if (parent) {
          const assigned = getButtonsForSwitch(parent);
          if (assigned.length > buttonCount) {
            throw new Error("Reduce assigned buttons before lowering the button count.");
          }
        }

        let metadata = {};
        try {
          metadata = JSON.parse(card.dataset.metadata || "{}");
        } catch (error) {
          metadata = {};
        }

        return {
          id: card.dataset.id || undefined,
          name: nameInput?.value.trim() || "",
          switch: switchValue,
          type: typeInput?.value || "momentary",
          button_count: buttonCount,
          has_buzzer: Boolean(buzzerInput?.checked),
          flash_leds: Boolean(flashInput?.checked),
          metadata,
        };
      }

      function ensureDriverOutputsClone(driver) {
        const outputs = Array.isArray(driver.outputs) ? driver.outputs.slice() : [];
        while (outputs.length < 4) {
          outputs.push(
            {
              id: "",
              slot: outputs.length,
              name: "",
              channels: [outputs.length],
              faulty: false,
              pwm: 0,
              target_pwm: 0,
              level: 0,
              min_pwm: 0,
              max_pwm: 255,
              disabled: false,
              metadata: {},
            }
          );
        }
        return outputs
          .slice(0, 4)
          .map((output, index) => ({
            id: output.id || "",
            slot: index,
            name: output.name || "",
            channels: Array.isArray(output.channels) ? output.channels.slice() : [],
            faulty: Boolean(output.faulty),
            pwm: Number(output.pwm) || 0,
            target_pwm: Number(output.target_pwm) || Number(output.pwm) || 0,
            level: Number(output.level) || 0,
            min_pwm: Number(output.min_pwm) || 0,
            max_pwm: Number(output.max_pwm) || 255,
            disabled: Boolean(output.disabled),
            metadata: typeof output.metadata === "object" && output.metadata !== null ? { ...output.metadata } : {},
          }));
      }

      function makeOutputKey(driverKey, outputId, slot) {
        const trimmedId = (outputId || "").trim();
        if (trimmedId) {
          return trimmedId;
        }
        const safeDriverKey = (driverKey || "driver").replace(/\s+/g, "_");
        return `${safeDriverKey}_slot${slot}`;
      }

      function normalizeLedName(name) {
        return (name || "").trim().toLowerCase();
      }

      function getLedNameSnapshot() {
        const snapshot = new Map();
        const allDrivers = [...state.drivers, ...state.drafts.drivers];
        allDrivers.forEach((driver) => {
          const driverKey = getItemKey(driver);
          if (!driverKey) {
            return;
          }
          const outputs = Array.isArray(driver.outputs) ? driver.outputs : [];
          outputs.forEach((output, index) => {
            const slot = Number.isInteger(output.slot) ? Number(output.slot) : index;
            const key = makeOutputKey(driverKey, output.id, slot);
            snapshot.set(key, output.name || "");
          });
        });

        if (driversContainer) {
          const cards = driversContainer.querySelectorAll(".driver-output-card");
          cards.forEach((card) => {
            const input = card.querySelector('[data-output-field="name"]');
            if (!input) {
              return;
            }
            const driverKey = card.dataset.driverKey || "";
            const outputId = card.dataset.outputId || "";
            let slot = Number.parseInt(card.dataset.slot ?? "0", 10);
            if (Number.isNaN(slot)) {
              slot = 0;
            }
            const key = makeOutputKey(driverKey, outputId, slot);
            snapshot.set(key, input.value);
          });
        }

        return snapshot;
      }

      function getUsedLedNamesSet() {
        const used = new Set();
        const snapshot = getLedNameSnapshot();
        snapshot.forEach((value) => {
          const normalized = normalizeLedName(value);
          if (normalized) {
            used.add(normalized);
          }
        });
        return used;
      }

      function createLedNameAllocator() {
        const used = getUsedLedNamesSet();
        let index = 1;
        return () => {
          while (used.has(`led${index}`)) {
            index += 1;
          }
          const name = `LED${index}`;
          used.add(`led${index}`);
          index += 1;
          return name;
        };
      }

      function isLedNameDuplicate(name, excludeKey) {
        const normalizedTarget = normalizeLedName(name);
        if (!normalizedTarget) {
          return false;
        }

        let duplicates = 0;
        const snapshot = getLedNameSnapshot();
        snapshot.forEach((value, key) => {
          if (excludeKey && key === excludeKey) {
            return;
          }
          if (normalizeLedName(value) === normalizedTarget) {
            duplicates += 1;
          }
        });

        return duplicates > 0;
      }

      function refreshLedNameValidation() {
        if (!driversContainer) {
          return;
        }

        const snapshot = getLedNameSnapshot();
        const counts = new Map();
        snapshot.forEach((value) => {
          const normalized = normalizeLedName(value);
          if (!normalized) {
            return;
          }
          counts.set(normalized, (counts.get(normalized) || 0) + 1);
        });

        const inputs = driversContainer.querySelectorAll('[data-output-field="name"]');
        inputs.forEach((input) => {
          const normalized = normalizeLedName(input.value);
          const isDuplicate = normalized && (counts.get(normalized) || 0) > 1;
          input.classList.toggle("input-error", Boolean(isDuplicate));
        });
      }

      async function ensureSvgTemplates() {
        if (svgPortCache.top && svgPortCache.bottom) {
          return;
        }
        if (!svgPortCache.promise) {
          svgPortCache.promise = Promise.all([
            fetch("/s2j_led_driver_static/led_driver_panel/RJ45-Top.svg"),
            fetch("/s2j_led_driver_static/led_driver_panel/RJ45-Bot.svg"),
          ])
            .then(async ([topResponse, bottomResponse]) => {
              if (!topResponse.ok) {
                throw new Error(`Failed to load RJ45-Top.svg (${topResponse.status})`);
              }
              if (!bottomResponse.ok) {
                throw new Error(`Failed to load RJ45-Bot.svg (${bottomResponse.status})`);
              }
              const [topText, bottomText] = await Promise.all([topResponse.text(), bottomResponse.text()]);
              const parser = new DOMParser();
              const topDoc = parser.parseFromString(topText, "image/svg+xml");
              const bottomDoc = parser.parseFromString(bottomText, "image/svg+xml");
              svgPortCache.top = document.importNode(topDoc.documentElement, true);
              svgPortCache.bottom = document.importNode(bottomDoc.documentElement, true);
            })
            .catch((error) => {
              svgPortCache.top = null;
              svgPortCache.bottom = null;
              throw error;
            })
            .finally(() => {
              svgPortCache.promise = null;
            });
        }
        return svgPortCache.promise;
      }

      function createPortCell(controller, driver, driverIndex, orientation) {
        const hasDriver = Boolean(driver && (driver.id || driver.__tempId));
        const cell = document.createElement("div");
        cell.className = "port-cell";
        cell.dataset.controllerId = controller.id || "";
        cell.dataset.driverIndex = String(driverIndex);

        let outputs = [];
        let isOn = false;
        let hasFault = false;
        let allDisabled = false;
        let driverName = `Driver ${driverIndex}`;

      if (hasDriver) {
        const key = driver.id || driver.__tempId;
        cell.dataset.driverKey = key;
        outputs = ensureDriverOutputsClone(driver);
          const activeOutputs = outputs.filter((output) => !output.disabled || output.faulty);
          isOn = activeOutputs.some((output) => Number(output.level ?? 0) > 0);
          hasFault = activeOutputs.some((output) => Boolean(output.faulty));
          allDisabled = outputs.length > 0 && activeOutputs.length === 0;
          driverName = driver.name || driver.id || driverName;
          cell.tabIndex = 0;
          cell.setAttribute("role", "button");
          if (allDisabled) {
            cell.classList.add("is-disabled");
          }
        } else {
          cell.dataset.disabled = "true";
          cell.classList.add("port-cell--empty");
          cell.setAttribute("aria-disabled", "true");
          cell.tabIndex = -1;
        }

        if (isOn) {
          cell.classList.add("is-on");
        }
        if (hasFault) {
          cell.classList.add("has-fault");
        }

        const svg = clonePortSvg(orientation === "top" ? "top" : "bottom", isOn, hasFault);
        cell.appendChild(svg);

        const nameEl = document.createElement("span");
        nameEl.className = "port-name";
        nameEl.textContent = driverName;
        const metaWrapper = document.createElement("div");
        metaWrapper.className = "port-metadata";
        metaWrapper.appendChild(nameEl);

        const statusEl = document.createElement("span");
        statusEl.className = "port-status";
        if (!hasDriver) {
          statusEl.textContent = "Unassigned";
        } else if (allDisabled) {
          statusEl.textContent = "Disabled";
        } else if (hasFault) {
          statusEl.textContent = "Fault";
        } else if (isOn) {
          statusEl.textContent = "On";
        } else {
          statusEl.textContent = "Off";
        }
        metaWrapper.appendChild(statusEl);
        cell.appendChild(metaWrapper);

        if (hasDriver) {
          const tooltip = [];
          tooltip.push(driverName || `Driver ${driverIndex}`);
          const controllerName = controller.name || controller.id || "Controller";
          tooltip.push(`Controller: ${controllerName}`);
          tooltip.push(`Index ${driverIndex}`);
          if (outputs.length) {
            const outputSummary = outputs
              .map((output) => {
                const channels = Array.isArray(output.channels) && output.channels.length
                  ? ` (${output.channels.join(",")})`
                  : "";
                const label = output.name || `Slot ${output.slot + 1}`;
                const disabledNote = output.disabled ? " [disabled]" : "";
                return `${label}${channels}${disabledNote}`;
              })
              .join(" · ");
            if (outputSummary) {
              tooltip.push(outputSummary);
            }
          }
          const hint = tooltip.filter(Boolean).join("\n");
          if (hint) {
            cell.title = hint;
            cell.setAttribute("aria-label", hint.replace(/\n+/g, ", "));
          }
        } else {
          const label = `Driver ${driverIndex} · Unassigned`;
          cell.title = label;
          cell.setAttribute("aria-label", label);
        }

        return cell;
      }

      function clonePortSvg(type, isOn, hasFault) {
        const template = type === "top" ? svgPortCache.top : svgPortCache.bottom;
        if (!template) {
          const placeholder = document.createElement("div");
          placeholder.textContent = "RJ45";
          placeholder.className = "port-placeholder";
          return placeholder;
        }
        const svg = template.cloneNode(true);
        svg.classList.add("rj45-image");
        applyIndicatorFill(svg.querySelector("#rect3803"), isOn, "#1dfe0a");
        applyIndicatorFill(svg.querySelector("#rect3805"), hasFault, "#ffd10a");
        return svg;
      }

      function applyIndicatorFill(element, active, color) {
        if (!element) {
          return;
        }
        if (active) {
          element.style.fill = color;
          element.style.opacity = "1";
        } else {
          element.style.fill = "#1f2937";
          element.style.opacity = "0.25";
        }
      }

      async function upsertRegistryItem(section, item, { refresh = true } = {}) {
        const stored = await apiRequest("POST", `/api/s2j_led_driver/${state.entryId}/registry/${section}`, { item });
        if (refresh) {
          await refreshAll({ showStatus: false, force: true });
        }
        return stored;
      }

      async function deleteRegistryItem(section, id) {
        await apiRequest("DELETE", `/api/s2j_led_driver/${state.entryId}/registry/${section}/${id}`);
        await refreshAll({ showStatus: false, force: true });
      }

      async function loadEntries() {
        clearError();
        entrySelect.innerHTML = "";
        const entries = await apiRequest("GET", "/api/s2j_led_driver/entries");
        if (!entries.length) {
          setError("No LED Driver integrations found. Add one under Settings → Devices & Services.");
          state.entryId = "";
          renderAll();
          stopAutoRefresh();
          statusEl.textContent = "";
          return;
        }
        entries.forEach((entry, index) => {
          const option = document.createElement("option");
          option.value = entry.entry_id;
          option.textContent = entry.title || entry.entry_id;
          entrySelect.appendChild(option);
          if (index === 0 && !state.entryId) {
            state.entryId = option.value;
          }
        });
        state.entryId = entrySelect.value || state.entryId;
        if (state.entryId) {
          entrySelect.value = state.entryId;
          await refreshAll({ showStatus: true, force: true });
        }
      }

withErrorNotice(async () => {
  await waitForHass();
  const versionEl = document.getElementById("panel-version");
  if (versionEl) {
    versionEl.textContent = PANEL_VERSION;
  }
  ensureSvgTemplates().catch((error) => console.debug("SVG templates unavailable", error));
  await loadEntries();
});
