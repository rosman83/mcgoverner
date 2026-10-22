import { Pills } from "./Pills";
import { UsagePopover, UpdateAlert } from "./UsagePopover";
import { GearIcon, SunIcon, MoonIcon, PowerIcon } from "./icons";
import { useTheme } from "../lib/useTheme";
import { quitApp } from "../lib/api";

const VIEWS = [
  { value: "dashboard", label: "Dashboard" },
  { value: "learn", label: "Learn" },
  { value: "drill", label: "Drill" },
  { value: "review", label: "Review" },
];

// Brand mark: a small bold wordmark with the cowboy hat emoji.
export function Navbar({ view, setView, usage }) {
  const { isDark, toggle } = useTheme();
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <div className="topbar-side">
          <span className="brand">🤠 MCGOVERNER</span>
        </div>
        <Pills options={VIEWS} value={view} onChange={setView} />
        <div className="topbar-side topbar-icons">
          <UpdateAlert usage={usage} />
          <UsagePopover usage={usage} />
          <button className="icon-btn" title="Settings" onClick={() => setView("settings")}>
            <GearIcon />
          </button>
          <button className="icon-btn" title="Toggle theme" onClick={toggle}>
            {isDark ? <SunIcon /> : <MoonIcon />}
          </button>
          <button
            className="icon-btn"
            title="Quit McGoverner"
            onClick={() => {
              quitApp();
              document.body.innerHTML =
                "<p style='padding:2rem;font:16px system-ui'>McGoverner has quit. You can close this tab.</p>";
            }}
          >
            <PowerIcon />
          </button>
        </div>
      </div>
    </header>
  );
}
