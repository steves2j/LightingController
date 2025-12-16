import { errorEl, statusEl } from "./dom.js";

export function formatNumber(value, decimals = 2) {
  if (!Number.isFinite(value)) {
    return "—";
  }
  return Number(value).toFixed(decimals);
}

export function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function setError(message) {
  if (errorEl) {
    errorEl.textContent = message;
  }
}

export function clearError() {
  if (errorEl) {
    errorEl.textContent = "";
  }
}

export async function withErrorNotice(action) {
  try {
    await action();
  } catch (error) {
    console.error(error);
    setError(error.message || "Request failed");
    if (statusEl) {
      statusEl.textContent = "";
    }
  }
}

export function formatTimestamp(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) {
    return "—";
  }
  try {
    return new Date(numeric).toLocaleTimeString();
  } catch (error) {
    console.debug("Failed to format timestamp", error);
    return "—";
  }
}

export function clone(value) {
  return JSON.parse(JSON.stringify(value ?? []));
}

export function makeTempId(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}
