/*
 * Drives the real app.js in jsdom against a live server, because the answer flow is a
 * UI state machine that backend tests cannot see. Two bugs shipped here before this
 * existed: options locked with no way back to editing, and feedback revealed before
 * the answer was recorded.
 *
 * Run:  ./run.sh          (in another shell, or point BASE at a running instance)
 *       node test_ui.js
 *
 * Needs jsdom. If it is not resolvable the script exits 0 with a skip notice so it
 * never blocks; JSDOM_PATH can point at an install elsewhere.
 */
const BASE = process.env.BASE || "http://localhost:8000";

let JSDOM;
try {
  ({ JSDOM } = require(process.env.JSDOM_PATH || "jsdom"));
} catch (e) {
  console.log("SKIP: jsdom not installed (npm i jsdom), UI tests not run");
  process.exit(0);
}

const assert = require("assert");

let passed = 0;
function check(name, fn) {
  return Promise.resolve()
    .then(fn)
    .then(() => { passed++; console.log("  ok  " + name); })
    .catch((e) => { console.error("  FAIL  " + name + "\n        " + e.message); process.exitCode = 1; });
}

async function api(path, opts) {
  const r = await fetch(BASE + path, opts);
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return r.json();
}

// The session is seeded by seed_ui_db.py into a throwaway database, so no API calls are
// spent and your real data is never touched. SID overrides it.
const SID = parseInt(process.env.SID || "1", 10);

async function loadApp(sid) {
  const html = await (await fetch(BASE + "/")).text();
  const dom = new JSDOM(html, {
    url: BASE + "/",
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
    // Must be installed before the page's scripts run: jsdom ships no fetch, and
    // node's rejects the relative URLs the app uses.
    beforeParse(window) {
      window.fetch = (url, opts) =>
        fetch(typeof url === "string" && url.startsWith("/") ? BASE + url : url, opts);
      window.confirm = () => true;
    },
  });
  const { window } = dom;
  // load may already have fired by the time we get here; waiting on the event then
  // would hang forever.
  if (window.document.readyState !== "complete") {
    await Promise.race([
      new Promise((res) => window.addEventListener("load", res)),
      new Promise((res) => setTimeout(res, 3000)),
    ]);
  }
  // Give the app's own startup fetches a beat to settle.
  await new Promise((r) => setTimeout(r, 400));
  if (typeof window.startExistingSession !== "function") {
    throw new Error("app.js did not load (startExistingSession missing)");
  }
  await window.startExistingSession(sid);
  await settle(window);
  return window;
}

// The UI kicks off fetches without awaiting them; poll until the card is rendered.
async function settle(window, ms = 2500) {
  const start = Date.now();
  while (Date.now() - start < ms) {
    await new Promise((r) => setTimeout(r, 60));
    const card = window.document.getElementById("question-card");
    if (card && card.querySelector(".option") && window.document.getElementById("submit-q")) return;
  }
  throw new Error("question card never rendered");
}

const $ = (w, id) => w.document.getElementById(id);
const opts = (w) => [...w.document.querySelectorAll(".option")];
const cls = (el) => [...el.classList];

async function main() {
  const sid = SID;

  await check("selecting an option does not grade or record it", async () => {
    const w = await loadApp(sid);
    const o = opts(w);
    const submit = $(w, "submit-q");
    assert.ok(submit.disabled, "Submit should start disabled");

    o[0].click();
    await new Promise((r) => setTimeout(r, 150));
    assert.ok(cls(o[0]).includes("selected"), "clicked option should be highlighted");
    assert.ok(!cls(o[0]).includes("correct") && !cls(o[0]).includes("wrong"),
      "no grading before Submit");
    assert.ok(!submit.disabled, "Submit should enable once something is picked");
    assert.ok(w.document.querySelector(".explanation").hidden, "explanation must stay hidden");
    assert.ok(o.every((b) => !b.disabled), "options must stay clickable before Submit");

    // Changing the highlight before submitting moves the selection.
    o[3].click();
    await new Promise((r) => setTimeout(r, 150));
    assert.ok(!cls(o[0]).includes("selected") && cls(o[3]).includes("selected"),
      "selection should move to the newly clicked option");

    const nav = await api(`/api/sessions/${sid}/nav`);
    assert.ok(nav.every((n) => !n.answered), "nothing should be recorded before Submit");
    w.close();
  });

  await check("submitting grades, locks, and offers Change answer", async () => {
    const w = await loadApp(sid);
    const o = opts(w);
    const submit = $(w, "submit-q");
    const correctIdx = o.findIndex((b) => b.dataset.correct === "1");
    const wrongIdx = correctIdx === 0 ? 1 : 0;

    o[wrongIdx].click();
    submit.click();
    await new Promise((r) => setTimeout(r, 500));

    assert.ok(cls(o[wrongIdx]).includes("wrong"), "picked option should be red");
    assert.ok(cls(o[correctIdx]).includes("correct"), "correct option should be green");
    assert.ok(o.every((b) => b.disabled), "options lock after Submit");
    assert.ok(!w.document.querySelector(".explanation").hidden, "explanation should show");
    assert.strictEqual(submit.textContent.trim(), "Change answer");
    assert.ok(!submit.disabled, "Change answer must be clickable — this was the dead end");
    w.close();
  });

  await check("Change answer unlocks, and the fix turns it green", async () => {
    const w = await loadApp(sid);
    // /next serves the first UNANSWERED question, so jump back deliberately via the
    // nav strip to the one the previous test got wrong.
    const missed = w.document.querySelector(".qnav-btn.bad");
    assert.ok(missed, "the wrong answer should be marked red in the nav strip");
    missed.click();
    await settle(w);
    await new Promise((r) => setTimeout(r, 400));

    const o = opts(w);
    const submit = $(w, "submit-q");
    const correctIdx = o.findIndex((b) => b.dataset.correct === "1");

    assert.strictEqual(submit.textContent.trim(), "Change answer",
      "revisiting an answered question should land in the graded state");

    submit.click();                                   // unlock
    await new Promise((r) => setTimeout(r, 150));
    assert.ok(o.every((b) => !b.disabled), "options should unlock");
    assert.ok(o.every((b) => !cls(b).includes("wrong") && !cls(b).includes("correct")),
      "stale feedback colours must clear when editing");
    assert.ok(w.document.querySelector(".explanation").hidden, "explanation hides while editing");

    o[correctIdx].click();
    $(w, "submit-q").click();
    await new Promise((r) => setTimeout(r, 600));

    assert.ok(cls(o[correctIdx]).includes("correct"), "corrected option should be green");
    assert.ok(o.every((b) => !cls(b).includes("wrong")), "nothing should still be red");

    const qid = parseInt(w.document.querySelector(".qnav-btn.current").dataset.qid, 10);
    const nav = await api(`/api/sessions/${sid}/nav`);
    const row = nav.find((n) => n.question_id === qid);
    assert.strictEqual(row.correct, 1, "server should record the revision as correct");
    assert.strictEqual(row.selected_index, correctIdx, "server should store the new choice");
    // The whole point: revising updates in place rather than counting as a new answer.
    const sess = await api(`/api/sessions/${sid}`);
    const answered = nav.filter((n) => n.answered).length;
    assert.strictEqual(sess.session.completed_count, answered,
      "progress count should match the number of answered questions");
    w.close();
  });

  await check("nav strip jumps between questions", async () => {
    const w = await loadApp(sid);
    await settle(w);
    const first = w.document.querySelector(".qnav-btn.current").dataset.qid;
    const buttons = [...w.document.querySelectorAll(".qnav-btn")];
    assert.ok(buttons.length >= 2, "nav should list every question");

    const other = buttons.find((b) => b.dataset.qid !== first);
    other.click();
    await settle(w);
    await new Promise((r) => setTimeout(r, 300));
    const nowCurrent = w.document.querySelector(".qnav-btn.current").dataset.qid;
    assert.strictEqual(nowCurrent, other.dataset.qid, "clicking a number should jump there");
    w.close();
  });

  await check("slide footnote expands inline and collapses again", async () => {
    const w = await loadApp(sid);
    const doc = w.document;

    // Summary-style citation: the markdown renderer turns "(slide 2)" into a footnote.
    const host = doc.createElement("div");
    host.innerHTML = w.marked("Receptors bind ligands. (slide 2)");
    doc.body.appendChild(host);

    const cite = host.querySelector(".slide-cite");
    assert.ok(cite, "a (slide N) citation should render as a footnote link");
    assert.strictEqual(cite.dataset.slide, "2");
    // Drill/review citations carry the lecture explicitly; summary ones fall back to
    // the lecture currently open in Learn.
    cite.dataset.lectureId = "1";

    cite.click();
    await new Promise((r) => setTimeout(r, 600));
    const panel = cite.nextElementSibling;
    assert.ok(panel && panel.classList.contains("cite-slide"), "clicking should insert a panel");
    assert.ok(!panel.hidden, "panel should be visible");
    assert.ok(/Slide 2/.test(panel.textContent), "panel should name the slide it expanded");
    assert.ok(cite.classList.contains("open"), "the citation should read as open");
    // Nothing may cover the page — this replaced a modal.
    assert.ok(doc.getElementById("slide-modal").hidden, "no modal should open");
    assert.notStrictEqual(w.document.body.style.overflow, "hidden", "page must stay scrollable");

    cite.click();
    await new Promise((r) => setTimeout(r, 150));
    assert.ok(cite.nextElementSibling.hidden, "clicking again should collapse it");
    assert.ok(!cite.classList.contains("open"));

    cite.click();
    await new Promise((r) => setTimeout(r, 150));
    assert.ok(!cite.nextElementSibling.hidden, "and re-expand without refetching");
    w.close();
  });

  console.log(`\n${passed} passed`);
}

main().catch((e) => { console.error("FATAL:", e.message); process.exit(1); });
