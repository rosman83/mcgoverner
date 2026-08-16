// Thin fetch wrapper - same API surface the old vanilla frontend used
// (backend is unchanged), just centralized instead of scattered fetch() calls.
async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body ? { "Content-Type": "application/json" } : undefined,
    ...options,
  });
  if (!res.ok && res.status !== 400) {
    throw new Error(`${options.method || "GET"} ${path} failed: ${res.status}`);
  }
  return res.json();
}

export const api = {
  get: (path) => request(path),
  post: (path, body) => request(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined }),
  patch: (path, body) => request(path, { method: "PATCH", body: JSON.stringify(body) }),
  del: (path) => request(path, { method: "DELETE" }),
};
