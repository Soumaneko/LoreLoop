import { desktopHeaders } from "./api.js";

const ENDPOINT = "/api/player/diagnostics";
const SECRET_PATTERNS = [
  /\bsk-[A-Za-z0-9_-]{12,}\b/g,
  /(authorization\s*[:=]\s*)([^\s,;]+)/gi,
  /(api[_-]?key\s*[:=]\s*)([^\s,;]+)/gi,
];

function sanitize(value, limit) {
  let result = String(value || "");
  for (const pattern of SECRET_PATTERNS) {
    result = result.replace(pattern, "$1[redacted]");
  }
  return result.slice(0, limit);
}

function send(payload) {
  fetch(ENDPOINT, {
    method: "POST",
    headers: desktopHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
    keepalive: true,
  }).catch(() => undefined);
}

export function reportClientError(error, context = "player_ui") {
  send({
    severity: "error",
    code: "player_frontend_error",
    message: sanitize(error?.message || error, 2000),
    context: sanitize(context, 120),
    stack: sanitize(error?.stack, 8000),
    path: sanitize(window.location.pathname, 500),
  });
}

export function installClientDiagnostics() {
  window.addEventListener("error", (event) => {
    reportClientError(event.error || event.message, "window_error");
  });
  window.addEventListener("unhandledrejection", (event) => {
    reportClientError(event.reason, "unhandled_rejection");
  });
}
