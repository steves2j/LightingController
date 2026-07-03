import { apiRequest, getAuthToken, resolveHass, waitForHass } from "./led_driver_panel/js/api.js";

const entryInput = document.getElementById("entry-id");
const deviceInput = document.getElementById("device-address");
const baudrateInput = document.getElementById("baudrate");
const connectButton = document.getElementById("connect");
const disconnectButton = document.getElementById("disconnect");
const clearButton = document.getElementById("clear");
const statusEl = document.getElementById("status");
const errorEl = document.getElementById("error");
const terminalEl = document.getElementById("terminal");

const term = new window.Terminal({
  cursorBlink: true,
  convertEol: false,
  scrollback: 5000,
  fontFamily: 'Menlo, Monaco, Consolas, "Liberation Mono", monospace',
  fontSize: 14,
  theme: {
    background: "#101316",
    foreground: "#e8edf2",
    cursor: "#f2cc60",
    selectionBackground: "#335c81",
  },
});
const fitAddon = new window.FitAddon.FitAddon();
term.loadAddon(fitAddon);
term.open(terminalEl);
fitAddon.fit();

let sessionId = "";
let streamUnsub = null;
let fallbackSocket = null;
let fallbackId = 1;
const fallbackPending = new Map();
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: false });

function setStatus(value) {
  statusEl.textContent = value;
}

function setError(message) {
  errorEl.textContent = message || "";
}

function bytesToBase64(bytes) {
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary);
}

function base64ToBytes(value) {
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

async function loadEntries() {
  await waitForHass().catch(() => undefined);
  const entries = await apiRequest("GET", "/api/s2j_led_driver/entries");
  if (!entryInput.value.trim() && entries[0]?.entry_id) {
    entryInput.value = entries[0].entry_id;
  }
}

function getHaConnection() {
  return resolveHass()?.connection || null;
}

async function ensureFallbackSocket() {
  if (fallbackSocket?.readyState === WebSocket.OPEN) {
    return fallbackSocket;
  }

  const token = getAuthToken();
  if (!token) {
    throw new Error("Unable to resolve a Home Assistant auth token.");
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const socket = new WebSocket(`${protocol}//${window.location.host}/api/websocket`);
  fallbackSocket = socket;

  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Timed out opening websocket.")), 8000);
    socket.addEventListener("message", function handleAuth(event) {
      const message = JSON.parse(event.data);
      if (message.type === "auth_required") {
        socket.send(JSON.stringify({ type: "auth", access_token: token }));
        return;
      }
      if (message.type === "auth_ok") {
        clearTimeout(timer);
        socket.removeEventListener("message", handleAuth);
        resolve();
        return;
      }
      if (message.type === "auth_invalid") {
        clearTimeout(timer);
        socket.removeEventListener("message", handleAuth);
        reject(new Error(message.message || "Websocket authentication failed."));
      }
    });
  });

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "event" && message.event) {
      handleTerminalEvent(message.event);
      return;
    }
    const pending = fallbackPending.get(message.id);
    if (!pending) {
      return;
    }
    if (message.type === "result") {
      fallbackPending.delete(message.id);
      if (message.success) {
        pending.resolve(message.result);
      } else {
        pending.reject(new Error(message.error?.message || "Websocket command failed."));
      }
    }
  });

  return socket;
}

async function sendMessage(message) {
  const connection = getHaConnection();
  if (connection?.sendMessagePromise) {
    return connection.sendMessagePromise(message);
  }
  const socket = await ensureFallbackSocket();
  const id = fallbackId;
  fallbackId += 1;
  socket.send(JSON.stringify({ ...message, id }));
  return new Promise((resolve, reject) => {
    fallbackPending.set(id, { resolve, reject });
  });
}

async function subscribeTerminal(message) {
  const connection = getHaConnection();
  if (connection?.subscribeMessage) {
    streamUnsub = await connection.subscribeMessage(handleTerminalEvent, message);
    return null;
  }
  const result = await sendMessage(message);
  streamUnsub = () => undefined;
  return result;
}

function handleTerminalEvent(event) {
  if (event.event === "data" && event.data) {
    term.write(decoder.decode(base64ToBytes(event.data)));
    return;
  }
  if (event.event === "status") {
    if (event.session_id) {
      sessionId = event.session_id;
      connectButton.disabled = true;
      disconnectButton.disabled = false;
      deviceInput.disabled = true;
      baudrateInput.disabled = true;
      entryInput.disabled = true;
      term.focus();
    }
    if (event.connected) {
      setStatus("Connected");
    } else {
      setStatus(event.error ? `Disconnected: ${event.error}` : "Disconnected");
    }
  }
}

async function connectTerminal() {
  setError("");
  const entryId = entryInput.value.trim();
  const port = deviceInput.value.trim();
  const baudrate = Number(baudrateInput.value) || 115200;
  if (!entryId || !port) {
    setError("Enter an entry id and serial device address.");
    return;
  }
  const result = await subscribeTerminal({
    type: "s2j_led_driver/terminal/connect_port",
    entry_id: entryId,
    port,
    baudrate,
  });
  if (result?.session_id) {
    handleTerminalEvent({ event: "status", connected: true, session_id: result.session_id });
  }
}

async function disconnectTerminal() {
  if (!sessionId) {
    return;
  }
  const closingSession = sessionId;
  sessionId = "";
  if (streamUnsub) {
    streamUnsub();
    streamUnsub = null;
  }
  await sendMessage({
    type: "s2j_led_driver/terminal/disconnect",
    entry_id: entryInput.value.trim(),
    session_id: closingSession,
  }).catch(() => undefined);
  connectButton.disabled = false;
  disconnectButton.disabled = true;
  deviceInput.disabled = false;
  baudrateInput.disabled = false;
  entryInput.disabled = false;
  setStatus("Disconnected");
}

term.onData((data) => {
  if (!sessionId) {
    return;
  }
  sendMessage({
    type: "s2j_led_driver/terminal/input",
    entry_id: entryInput.value.trim(),
    session_id: sessionId,
    data: bytesToBase64(encoder.encode(data)),
  }).catch((error) => setError(error.message || "Failed to send terminal input."));
});

function resizeTerminal() {
  fitAddon.fit();
  if (!sessionId) {
    return;
  }
  sendMessage({
    type: "s2j_led_driver/terminal/resize",
    entry_id: entryInput.value.trim(),
    session_id: sessionId,
    cols: term.cols,
    rows: term.rows,
  }).catch(() => undefined);
}

connectButton.addEventListener("click", () => connectTerminal().catch((error) => setError(error.message)));
disconnectButton.addEventListener("click", () => disconnectTerminal().catch((error) => setError(error.message)));
clearButton.addEventListener("click", () => term.clear());
window.addEventListener("resize", resizeTerminal);
window.addEventListener("beforeunload", () => {
  disconnectTerminal();
});

loadEntries()
  .then(() => {
    setStatus("Disconnected");
    term.writeln("Enter a serial device address and connect.");
  })
  .catch((error) => setError(error.message || "Failed to initialize terminal."));
