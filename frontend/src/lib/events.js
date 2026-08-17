// Dashboard, Learn, and Drill each fetch their own lecture list independently
// and stay mounted for the whole session (see App.jsx) - importing a lecture
// on Dashboard never told Learn's already-mounted list to refetch, so a new
// lecture only showed up after a full page reload. This is a tiny event bus
// so any view that mutates lectures (import, rename, retag, re-week) can
// notify every other view that has its own copy of that list.
const LECTURES_CHANGED = "lectures-changed";

export function notifyLecturesChanged() {
  window.dispatchEvent(new Event(LECTURES_CHANGED));
}

export function onLecturesChanged(cb) {
  window.addEventListener(LECTURES_CHANGED, cb);
  return () => window.removeEventListener(LECTURES_CHANGED, cb);
}
