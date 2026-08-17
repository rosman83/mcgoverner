import { Pills } from "./Pills";
import { UsagePopover } from "./UsagePopover";
import { GearIcon, SunIcon, MoonIcon, HatIcon } from "./icons";
import { useTheme } from "../lib/useTheme";

const VIEWS = [
  { value: "dashboard", label: "Dashboard" },
  { value: "learn", label: "Learn" },
  { value: "drill", label: "Drill" },
  { value: "review", label: "Review" },
];

// The cowboy hat from the landing page is the one bit of brand identity kept
// here, as a small logo mark - everything else is still just the pill
// switcher, no logo text.
export function Navbar({ view, setView, usage }) {
  const { isDark, toggle } = useTheme();
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <div className="topbar-side">
          <HatIcon />
        </div>
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
      </div>
    </header>
  );
}
