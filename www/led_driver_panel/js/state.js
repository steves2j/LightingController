import { makeTempId } from "./utils.js";

export const state = {
  entryId: "",
  controllers: [],
  drivers: [],
  groups: [],
  switches: [],
  buttons: [],
  learnedButtons: [],
  ledOutputs: [],
  pendingLedPwm: {},
  activeGroupSlider: null,
  ssrEntries: [],
  ssrBaseAddress: 0,
  patchPanelPorts: [],
  selectedPatchPort: null,
  drafts: {
    controllers: [],
    drivers: [],
    groups: [],
    switches: [],
    buttons: [],
    ssrs: [],
  },
  editing: {
    controllers: new Set(),
    drivers: new Set(),
    groups: new Set(),
    switches: new Set(),
    buttons: new Set(),
    ssrs: new Set(),
  },
  groupState: {},
};

export function isEditing() {
  return (
    state.editing.controllers.size > 0 ||
    state.editing.drivers.size > 0 ||
    state.editing.groups.size > 0 ||
    state.editing.switches.size > 0 ||
    state.editing.buttons.size > 0 ||
    state.editing.ssrs.size > 0 ||
    state.drafts.controllers.length > 0 ||
    state.drafts.drivers.length > 0 ||
    state.drafts.groups.length > 0 ||
    state.drafts.switches.length > 0 ||
    state.drafts.buttons.length > 0 ||
    state.drafts.ssrs.length > 0
  );
}

export function getItemKey(item) {
  return item.__tempId || item.id;
}

export function addDraft(type, draft) {
  draft.__tempId = makeTempId(type);
  state.drafts[type].push(draft);
  state.editing[type].add(draft.__tempId);
  return draft.__tempId;
}

export function removeDraft(type, key) {
  const list = state.drafts[type];
  const index = list.findIndex((item) => item.__tempId === key);
  if (index !== -1) {
    list.splice(index, 1);
  }
}

export function syncEditingSets() {
  syncEditingSet("controllers", [...state.drafts.controllers, ...state.controllers]);
  syncEditingSet("drivers", [...state.drafts.drivers, ...state.drivers]);
  syncEditingSet("groups", [...state.drafts.groups, ...state.groups]);
  syncEditingSet("switches", [...state.drafts.switches, ...state.switches]);
  syncEditingSet("buttons", [...state.drafts.buttons, ...state.buttons]);
  syncEditingSet("ssrs", [...state.drafts.ssrs, ...state.ssrEntries]);
}

export function syncEditingSet(type, items) {
  const validKeys = new Set(items.map(getItemKey));
  const set = state.editing[type];
  for (const key of Array.from(set)) {
    if (!validKeys.has(key)) {
      set.delete(key);
    }
  }
}

export function getControllerDisplayName(controllerId) {
  if (!controllerId) {
    return "Unassigned";
  }
  const controller = state.controllers.find((ctrl) => ctrl.id === controllerId);
  return controller?.name || controllerId;
}

export function getLedDisplayName(outputId) {
  if (!outputId) {
    return "LED";
  }
  const descriptor = state.ledOutputs.find((output) => output.id === outputId);
  if (descriptor) {
    return descriptor.name || `${descriptor.driver_name || descriptor.driver_id} · slot ${descriptor.slot + 1}`;
  }
  return outputId;
}
