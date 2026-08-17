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

// Same problem, different list: Learn's "Resume a session" card and Review's
// "Past sessions" list each fetch /api/sessions independently and both stay
// mounted - deleting or starting a session in one left the other showing a
// stale row until a manual refresh.
const SESSIONS_CHANGED = "sessions-changed";

export function notifySessionsChanged() {
  window.dispatchEvent(new Event(SESSIONS_CHANGED));
}

export function onSessionsChanged(cb) {
  window.addEventListener(SESSIONS_CHANGED, cb);
  return () => window.removeEventListener(SESSIONS_CHANGED, cb);
}
