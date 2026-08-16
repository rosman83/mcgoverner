import { Pills } from "./Pills";
import { UsagePopover } from "./UsagePopover";
import { GearIcon, SunIcon, MoonIcon } from "./icons";
import { useTheme } from "../lib/useTheme";

const VIEWS = [
  { value: "dashboard", label: "Dashboard" },
  { value: "learn", label: "Learn" },
  { value: "drill", label: "Drill" },
  { value: "review", label: "Review" },
];

// No brand text/logo - the pill switcher itself is the only navigation
// chrome. Usage and settings are icons, not permanent text/tabs.
export function Navbar({ view, setView, usage }) {
  const { isDark, toggle } = useTheme();
  return (
    <header className="topbar">
      <div className="topbar-side" />
      <Pills options={VIEWS} value={view} onChange={setView} />
      <div className="topbar-side topbar-icons">
        <UsagePopover usage={usage} />
        <button className="icon-btn" title="Settings" onClick={() => setView("settings")}>
          <GearIcon />
        </button>
        <button className="icon-btn" title="Toggle theme" onClick={toggle}>
          {isDark ? <SunIcon /> : <MoonIcon />}
        </button>
      </div>
    </header>
  );
}
