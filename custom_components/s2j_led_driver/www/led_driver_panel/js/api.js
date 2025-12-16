export function resolveHass() {
  const parent = window.parent;
  if (!parent) {
    return null;
  }
  if (parent.hass) {
    return parent.hass;
  }
  try {
    const root = parent.document?.querySelector("home-assistant");
    if (root?.hass) {
      return root.hass;
    }
    const main = root?.shadowRoot?.querySelector("home-assistant-main");
    if (main?.hass) {
      return main.hass;
    }
  } catch (error) {
    console.debug("Failed to resolve hass instance", error);
  }
  return null;
}

export function getAuthToken() {
  const hass = resolveHass();
  if (hass?.auth?.data?.access_token) {
    return hass.auth.data.access_token;
  }
  if (hass?.auth?.accessToken) {
    return hass.auth.accessToken;
  }
  try {
    const storage = window.parent?.localStorage || localStorage;
    const tokens = storage?.getItem("hassTokens");
    if (tokens) {
      const parsed = JSON.parse(tokens);
      return parsed?.access_token;
    }
  } catch (error) {
    console.debug("Unable to read hassTokens", error);
  }
  return null;
}

export async function apiRequest(method, path, payload) {
  const hass = resolveHass();
  const apiPath = path.startsWith("/api/") ? path.slice(5) : path;

  if (hass?.callApi) {
    if (payload === undefined) {
      return hass.callApi(method, apiPath);
    }
    return hass.callApi(method, apiPath, payload);
  }

  const headers = {};
  if (payload !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  const token = getAuthToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(path, {
    method,
    headers,
    body: payload === undefined ? undefined : JSON.stringify(payload),
    credentials: "same-origin",
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || response.statusText);
  }

  if (response.status === 204) {
    return null;
  }

  return response.json();
}

export function sendGroupCommand(entryId, groupId, turnOn) {
  return apiRequest("POST", `/api/s2j_led_driver/${entryId}/command`, {
    command: "set_group",
    group_id: groupId,
    on: turnOn,
  });
}

export function setControllerPolling(entryId, controllerId, enabled) {
  return apiRequest("POST", `/api/s2j_led_driver/${entryId}/command`, {
    command: "set_controller_poll",
    controller_id: controllerId,
    enabled,
  });
}

export function setControllerCanInterface(entryId, controllerId, enabled) {
  return apiRequest("POST", `/api/s2j_led_driver/${entryId}/registry/controllers`, {
    item: {
      id: controllerId,
      has_can_interface: enabled,
    },
  });
}

export function setLedOutputTargets(entryId, targets) {
  if (!entryId) {
    return Promise.reject(new Error("Select an integration entry before updating LEDs."));
  }
  return apiRequest("POST", `/api/s2j_led_driver/${entryId}/outputs/targets`, {
    targets,
  });
}

export function sendLedConfigs(entryId, configs) {
  if (!entryId) {
    return Promise.reject(new Error("Select an integration entry before updating LEDs."));
  }
  if (!Array.isArray(configs) || !configs.length) {
    return Promise.resolve();
  }
  return apiRequest("POST", `/api/s2j_led_driver/${entryId}/command`, {
    command: "set_led_config",
    configs,
  });
}

export function sendGroupPwmTargets(entryId, groupId, targets) {
  if (!entryId) {
    return Promise.reject(new Error("Select an integration entry before updating LEDs."));
  }
  return apiRequest("POST", `/api/s2j_led_driver/${entryId}/command`, {
    command: "set_group_pwm",
    group_id: groupId,
    targets,
  });
}

export function setSsrBaseAddress(entryId, baseAddress) {
  if (!entryId) {
    return Promise.reject(new Error("Select an integration entry before updating SSR settings."));
  }
  return apiRequest("POST", `/api/s2j_led_driver/${entryId}/registry/ssr/base`, {
    base_address: baseAddress,
  });
}

export function upsertSsrEntry(entryId, item) {
  if (!entryId) {
    return Promise.reject(new Error("Select an integration entry before updating SSR settings."));
  }
  return apiRequest("POST", `/api/s2j_led_driver/${entryId}/registry/ssr/entries`, {
    item,
  });
}

export function deleteSsrEntry(entryId, ssrId) {
  if (!entryId) {
    return Promise.reject(new Error("Select an integration entry before updating SSR settings."));
  }
  return apiRequest("DELETE", `/api/s2j_led_driver/${entryId}/registry/ssr/entries/${ssrId}`);
}

export function setSsrState(entryId, ssrId, turnOn) {
  if (!entryId) {
    return Promise.reject(new Error("Select an integration entry before updating SSR settings."));
  }
  return apiRequest("POST", `/api/s2j_led_driver/${entryId}/command`, {
    command: "set_ssr_state",
    ssr_id: ssrId,
    on: turnOn,
  });
}

export function upsertPatchPanelPort(entryId, item) {
  if (!entryId) {
    return Promise.reject(new Error("Select an integration entry before updating patch panel metadata."));
  }
  return apiRequest("POST", `/api/s2j_led_driver/${entryId}/registry/patch_panel/ports`, {
    item,
  });
}

export function deleteLearnedButton(entryId, key) {
  if (!entryId) {
    return Promise.reject(new Error("Select an integration entry before deleting discoveries."));
  }
  return apiRequest("DELETE", `/api/s2j_led_driver/${entryId}/registry/learned_buttons/${key}`);
}

export async function waitForHass(timeoutMs = 5000) {
  const interval = 100;
  const deadline = Date.now() + timeoutMs;
  while (true) {
    if (resolveHass()) {
      return;
    }
    if (Date.now() > deadline) {
      throw new Error("Timed out waiting for Home Assistant API context");
    }
    await new Promise((resolve) => setTimeout(resolve, interval));
  }
}
