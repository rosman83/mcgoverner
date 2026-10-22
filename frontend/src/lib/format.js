export function fmtDate(s) {
  if (!s) return "";
  const d = new Date(s.replace(" ", "T") + "Z");
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const sameYear = d.getFullYear() === now.getFullYear();
  return (
    d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: sameYear ? undefined : "numeric" }) +
    " · " +
    d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })
  );
}

// A lecture's "week" is a free-text label ("3", "A", "Midterm review"). The
// DB column has INTEGER affinity, so plain numbers come back as numbers and
// everything else as text - always go through these instead of using it raw.
export function weekText(w) {
  return w === null || w === undefined ? "" : String(w).trim();
}
// Bare numbers read as "Week 3"; anything else is shown exactly as typed.
export function weekLabel(w) {
  const t = weekText(w);
  return /^\d+$/.test(t) ? `Week ${t}` : t;
}
export function weekCompare(a, b) {
  return weekText(a).localeCompare(weekText(b), undefined, { numeric: true, sensitivity: "base" });
}

// Course strand is a free-text label. The three original strands keep their
// capitalised names; anything else is shown exactly as typed.
const KNOWN_TAGS = { foundations: "Foundations", doctoring: "Doctoring", anatomy: "Anatomy" };
export function tagLabel(t) {
  const v = (t ?? "").toString().trim() || "foundations";
  return KNOWN_TAGS[v] || v;
}
