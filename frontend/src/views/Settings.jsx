import { useEffect, useState } from "react";
import { api } from "../lib/api";

function Field({ f, values, setValues }) {
  if (f.type === "bool") {
    return (
      <div className="settings-field settings-field-bool">
        <label>
          <input
            type="checkbox"
            checked={values[f.name] ?? f.value ?? false}
            onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.checked }))}
          />
          {f.label}
        </label>
        <div className="help muted">{f.help}</div>
      </div>
    );
  }
  const isSecret = f.type === "secret";
  return (
    <div className="settings-field">
      <label>{f.label}</label>
      <input
        type={isSecret ? "password" : "text"}
        placeholder={isSecret && f.set ? "Enter a new key to replace it" : ""}
        value={values[f.name] ?? (isSecret ? "" : f.display) ?? ""}
        onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
        autoComplete="off"
      />
      {isSecret && (
        <div className={f.set ? "status-set" : "help muted"}>
          {f.set ? `Set (${f.display})` : "Not set"}
        </div>
      )}
      <div className="help muted">{f.help}</div>
    </div>
  );
}

export function Settings({ onConfigChange }) {
  const [cfg, setCfg] = useState(null);
  const [values, setValues] = useState({});
  const [status, setStatus] = useState("");
  const [error, setError] = useState(false);

  useEffect(() => {
    api.get("/api/config").then((c) => { setCfg(c); onConfigChange?.(c.configured); });
  }, [onConfigChange]);

  async function save() {
    setStatus("Saving...");
    setError(false);
    const updates = {};
    for (const f of cfg.fields) {
      if (f.type === "bool") {
        updates[f.name] = String(values[f.name] ?? f.value ?? false);
      } else if (f.name in values) {
        updates[f.name] = values[f.name];
      }
    }
    try {
      const result = await api.post("/api/config", updates);
      setCfg(result);
      setValues({});
      onConfigChange?.(result.configured);
      if (result.error) {
        setStatus(result.error);
        setError(true);
      } else {
        setStatus(result.configured ? "Saved." : "Saved, but no API key is set yet.");
      }
    } catch (e) {
      setStatus("Save failed: " + e.message);
      setError(true);
    }
  }

  if (!cfg) return <div className="muted">Loading…</div>;

  const primary = cfg.fields.filter((f) => !f.advanced);
  const advanced = cfg.fields.filter((f) => f.advanced);

  return (
    <div className="solo-card" style={{ maxWidth: 480 }}>
      {!cfg.configured && (
        <div className="config-banner">Add your OpenRouter key below to start generating summaries and questions.</div>
      )}
      <div className="card">
        <h3>API configuration</h3>
        <p className="muted" style={{ fontSize: 13.5 }}>
          Values already set in your <code>.env</code> file show up here automatically — saving
          here writes back to the same <code>.env</code>.
        </p>
        {primary.map((f) => <Field key={f.name} f={f} values={values} setValues={setValues} />)}
        {advanced.length > 0 && (
          <details className="settings-advanced">
            <summary>Advanced</summary>
            <div>
              {advanced.map((f) => <Field key={f.name} f={f} values={values} setValues={setValues} />)}
            </div>
          </details>
        )}
        <div className="settings-actions">
          <button className="btn" onClick={save}>Save</button>
          <span className={`muted ${error ? "error" : ""}`}>{status}</span>
        </div>
      </div>
    </div>
  );
}
