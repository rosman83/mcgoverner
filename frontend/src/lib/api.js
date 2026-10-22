// Thin fetch wrapper - same API surface the old vanilla frontend used
// (backend is unchanged), just centralized instead of scattered fetch() calls.
//
// Every unhandled backend exception now comes back as JSON with a short
// error_id and message (see app/main.py's exception_handler) instead of a
// bare "Internal Server Error". A 500 here is a real bug, not a per-view
// concern each screen should have to handle individually, so it's also
// broadcast as a window event - App.jsx shows a toast for anything a view
// didn't already catch itself, so a crash is never just silently vague.
// Our exception handler sends {message, error_id}; FastAPI's HTTPException
// sends {detail: "..."} (validation errors send a detail list - not shown).
function errorMessage(body) {
  if (body?.message) return `${body.message}${body.error_id ? ` (ref: ${body.error_id})` : ""}`;
  if (typeof body?.detail === "string") return body.detail;
  return null;
}

async function request(path, options = {}) {
  let res;
  try {
    // FormData (file/session forms) must let the browser set its own multipart header.
    const json = options.body && !(options.body instanceof FormData);
    res = await fetch(path, {
      headers: json ? { "Content-Type": "application/json" } : undefined,
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
    const message = errorMessage(body) || `${options.method || "GET"} ${path} failed: ${res.status}`;
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
  post: (path, body) => request(path, {
    method: "POST",
    body: body === undefined || body instanceof FormData ? body : JSON.stringify(body),
  }),
  patch: (path, body) => request(path, { method: "PATCH", body: JSON.stringify(body) }),
  del: (path) => request(path, { method: "DELETE" }),
};

// fetch() has no upload-progress event, only XHR does - used for file
// uploads (lecture/handout import) so the UI can show real transfer
// progress instead of an unmoving "Importing…" label. onProgress gets
// {phase: "uploading", pct} while bytes are sending, then {phase:
// "processing"} once the browser has sent everything and is waiting on the
// server's response (parsing/OCR/etc, which has no progress signal at all).
export function uploadWithProgress(path, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.({ phase: "uploading", pct: Math.round((e.loaded / e.total) * 100) });
    };
    xhr.upload.onload = () => onProgress?.({ phase: "processing", pct: 100 });
    xhr.onload = () => {
      let body = null;
      try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      // A 500 used to resolve as if it succeeded, then crash reading
      // res.imported - the user saw "Cannot read properties of undefined".
      if (xhr.status >= 400 || !body) {
        reject(new Error(errorMessage(body) || `Upload failed (${xhr.status || "no response"}). The file may be too large or the server stopped.`));
      } else {
        resolve(body);
      }
    };
    xhr.onerror = () => reject(new Error("Could not reach the server during upload."));
    xhr.send(formData);
  });
}

// Fire-and-forget: /api/quit kills the server process shortly after responding, so
// the connection dropping is expected, not an error worth throwing on.
export function quitApp() {
  fetch("/api/quit", { method: "POST" }).catch(() => {});
}
