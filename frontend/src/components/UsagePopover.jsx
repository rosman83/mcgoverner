import { useState } from "react";
import { GaugeIcon } from "./icons";

function fmtTokens(n) {
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return String(n);
}

// Was a persistent text chip in the navbar ("gemini-3.7-flash · 12.4k tokens
// today") - now a single icon, details on click instead of always-on text.
export function UsagePopover({ usage }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ position: "relative" }}>
      <button className="icon-btn" title="Token usage" onClick={() => setOpen((o) => !o)} onBlur={() => setTimeout(() => setOpen(false), 150)}>
        <GaugeIcon />
      </button>
      {open && usage && (
        <div className="card" style={{ position: "absolute", right: 0, top: 40, width: 240, zIndex: 30 }}>
          <div style={{ fontWeight: 700, marginBottom: 6 }}>{usage.provider} / {usage.model}</div>
          {usage.version && (
            <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>build {usage.version}</div>
          )}
          <div className="muted" style={{ fontSize: 13 }}>
            Today: {fmtTokens(usage.today.total_tokens)} tokens, {usage.today.calls} calls
            {usage.today.cache_hit_pct ? ` (${usage.today.cache_hit_pct}% cached)` : ""}
          </div>
          <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>
            All time: {fmtTokens(usage.all_time.total_tokens)} tokens
          </div>
          {usage.today_by_kind?.length > 0 && (
            <div style={{ marginTop: 8, borderTop: "1px solid var(--border)", paddingTop: 8 }}>
              {usage.today_by_kind.map((k) => (
                <div key={k.kind} className="muted" style={{ fontSize: 12.5, display: "flex", justifyContent: "space-between" }}>
                  <span>{k.kind || "other"}</span>
                  <span>{fmtTokens(k.tokens)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
