import { useEffect, useState, useCallback } from "react";
import { Navbar } from "./components/Navbar";
import { Dashboard } from "./views/Dashboard";
import { Learn } from "./views/Learn";
import { Drill } from "./views/Drill";
import { Review } from "./views/Review";
import { Settings } from "./views/Settings";
import { ErrorToast } from "./components/ErrorToast";
import { api } from "./lib/api";

export default function App() {
  const [view, setView] = useState("dashboard");
  const [configured, setConfigured] = useState(true); // optimistic until checked
  const [usage, setUsage] = useState(null);
  // A session id to open in Drill, set from outside Drill's own mounted tree
  // (the Review tab's Resume/Review buttons, Learn's "Resume a session" card).
  const [pendingSessionId, setPendingSessionId] = useState(null);

  const handleConfigChange = useCallback((isConfigured) => {
    setConfigured(isConfigured);
  }, []);

  useEffect(() => {
    api.get("/api/config").then((c) => {
      setConfigured(c.configured);
      if (!c.configured) setView("settings");
    });
  }, []);

  useEffect(() => {
    const refresh = () => api.get("/api/usage").then(setUsage).catch(() => {});
    refresh();
    const id = setInterval(refresh, 60000);
    return () => clearInterval(id);
  }, []);

  function changeView(v) {
    if (!configured && v !== "settings") return;
    setView(v);
  }

  function openDrillSession(id) {
    setPendingSessionId(id);
    changeView("drill");
  }

  return (
    <div className="shell">
      <Navbar view={view} setView={changeView} usage={usage} />
      <main>
        {/* Dashboard/Learn/Drill stay mounted across nav so their data, scroll
            position, and (for Drill) an in-progress timed session survive
            switching away and back - unmounting would refetch + flash
            "Loading…", or worse, drop a running quiz timer. */}
        <div style={{ display: view === "dashboard" ? "block" : "none" }}>
          <Dashboard />
        </div>
        <div style={{ display: view === "learn" ? "block" : "none" }}>
          <Learn onOpenSession={openDrillSession} />
        </div>
        <div style={{ display: view === "drill" ? "block" : "none" }}>
          <Drill openSessionId={pendingSessionId} onOpenSessionHandled={() => setPendingSessionId(null)} />
        </div>
        <div style={{ display: view === "review" ? "block" : "none" }}>
          <Review onOpenSession={openDrillSession} />
        </div>
        {view === "settings" && <Settings onConfigChange={handleConfigChange} />}
      </main>
      <ErrorToast />
    </div>
  );
}
