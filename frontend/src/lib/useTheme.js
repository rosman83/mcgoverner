import { useEffect, useState } from "react";

function getInitialTheme() {
  try {
    const saved = localStorage.getItem("mcgoverner-theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    /* localStorage unavailable - fall through to system preference */
  }
  return null; // null = follow system preference, no explicit override yet
}

// Sun/moon toggle with a system-preference default - replaces the old
// six-way theme picker. One explicit override stored once the user picks.
export function useTheme() {
  const [theme, setTheme] = useState(getInitialTheme);

  useEffect(() => {
    if (theme) {
      document.documentElement.setAttribute("data-theme", theme);
      try { localStorage.setItem("mcgoverner-theme", theme); } catch { /* ignore */ }
    } else {
      document.documentElement.removeAttribute("data-theme");
      try { localStorage.removeItem("mcgoverner-theme"); } catch { /* ignore */ }
    }
  }, [theme]);

  const systemDark = typeof window !== "undefined"
    && window.matchMedia("(prefers-color-scheme: dark)").matches;
  const isDark = theme ? theme === "dark" : systemDark;

  return { isDark, toggle: () => setTheme(isDark ? "light" : "dark") };
}
