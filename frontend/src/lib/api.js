// Thin fetch wrapper - same API surface the old vanilla frontend used
// (backend is unchanged), just centralized instead of scattered fetch() calls.
//
// Every unhandled backend exception now comes back as JSON with a short
// error_id and message (see app/main.py's exception_handler) instead of a
// bare "Internal Server Error". A 500 here is a real bug, not a per-view
// concern each screen should have to handle individually, so it's also
// broadcast as a window event - App.jsx shows a toast for anything a view
// didn't already catch itself, so a crash is never just silently vague.
async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      headers: options.body ? { "Content-Type": "application/json" } : undefined,
      ...options,
    });
  } catch {
    const err = new Error("Could not reach the server. It may still be starting, or has stopped.");
    window.dispatchEvent(new CustomEvent("api-error", { detail: { message: err.message } }));
    throw err;
  }

  if (!res.ok && res.status !== 400) {
    let body = null;
    try { body = await res.json(); } catch { /* not JSON - fine, body stays null */ }
    const message = body?.message
      ? `${body.message}${body.error_id ? ` (ref: ${body.error_id})` : ""}`
      : `${options.method || "GET"} ${path} failed: ${res.status}`;
    const err = new Error(message);
    err.status = res.status;
    err.body = body;
    if (res.status >= 500) window.dispatchEvent(new CustomEvent("api-error", { detail: { message, status: res.status } }));
    throw err;
  }
  return res.json();
}

export const api = {
  get: (path) => request(path),
  post: (path, body) => request(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined }),
  patch: (path, body) => request(path, { method: "PATCH", body: JSON.stringify(body) }),
  del: (path) => request(path, { method: "DELETE" }),
};
