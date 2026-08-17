import { useEffect, useState } from "react";

// api.js broadcasts an "api-error" window event for anything that reaches a
// 500 or a dead server - this is the catch-all surface for that, so a crash
// is never just a silently vague failure. Individual views can still show
// their own inline error state for expected cases (missing config, etc.);
// this only fires for the unexpected kind.
export function ErrorToast() {
  const [msg, setMsg] = useState(null);

  useEffect(() => {
    function onError(e) {
      setMsg(e.detail?.message || "Something went wrong.");
    }
    window.addEventListener("api-error", onError);
    return () => window.removeEventListener("api-error", onError);
  }, []);

  if (!msg) return null;
  return (
    <div className="error-toast">
      <span>{msg}</span>
      <button className="icon-btn" onClick={() => setMsg(null)}>×</button>
    </div>
  );
}
