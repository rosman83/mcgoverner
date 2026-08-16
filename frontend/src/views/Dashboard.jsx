import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { InfoPopup } from "../components/InfoPopup";
import { Dropzone } from "../components/Dropzone";
import { Pills } from "../components/Pills";
import { Spinner } from "../components/Spinner";

const PAGE = 6;
const UPLOAD_MODES = [
  { value: "lectures", label: "Add lectures" },
  { value: "questions", label: "Question handouts" },
];

function pct(x) {
  return x === null || x === undefined ? "—" : Math.round(x * 100) + "%";
}
function accClass(x) {
  if (x === null || x === undefined) return "";
  if (x < 0.6) return "acc-bad";
  if (x < 0.8) return "acc-mid";
  return "acc-good";
}
function shortLabel(s, n = 60) {
  s = s || "";
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function Stat({ label, value, cls }) {
  return (
    <div className="stat-item">
      <div className={`stat-value ${cls || ""}`}>{value}</div>
      <div className="stat-label muted">{label}</div>
    </div>
  );
}

function Projection({ p }) {
  if (!p || !p.slides_total) {
    return <div className="muted">Import a lecture to see a projection.</div>;
  }
  if (p.slides_mastered >= p.slides_total) {
    return <div className="proj-headline">All {p.slides_total} slides mastered.</div>;
  }
  const acc = p.accuracy_is_assumed
    ? `assuming ${Math.round(p.accuracy * 100)}% accuracy until you answer some`
    : `at your current ${Math.round(p.accuracy * 100)}% accuracy`;

  return (
    <div>
      <div className="proj-headline">
        <span className="proj-num">{p.questions_remaining}</span> questions to{" "}
        <span className="proj-num">{p.target_pct}%</span> mastery
        <InfoPopup>
          Simulated {p.trials}× through the real slide-picking logic, session by session.
          The last few percent cost far more than the first, which is why the target is{" "}
          {p.target_pct}%, not 100%. Estimate: ≈{p.sessions_remaining} session
          {p.sessions_remaining === 1 ? "" : "s"} (likely range {p.questions_p25}–{p.questions_p75}{" "}
          questions), {acc}.
        </InfoPopup>
      </div>
      <div className="proj-bar"><span style={{ width: `${p.mastery_pct}%` }} /></div>
      <div className="proj-sub muted">{p.slides_mastered}/{p.slides_total} slides mastered · {p.mastery_pct}%</div>
    </div>
  );
}

export function Dashboard() {
  const [d, setD] = useState(null);
  const [proj, setProj] = useState(null);
  const [err, setErr] = useState(null);
  const [importMsg, setImportMsg] = useState("");
  const [importing, setImporting] = useState(false);
  const [lectures, setLectures] = useState([]);
  const [qsLectureId, setQsLectureId] = useState("");
  const [qsMsg, setQsMsg] = useState("");
  const [uploadMode, setUploadMode] = useState("lectures");
  const [weakShown, setWeakShown] = useState(PAGE);
  const [gapsShown, setGapsShown] = useState(PAGE);

  async function load() {
    try {
      const [dash, projection, lex] = await Promise.all([
        api.get("/api/dashboard"),
        api.get("/api/mastery_projection"),
        api.get("/api/lectures"),
      ]);
      setD(dash);
      setProj(projection);
      setLectures(lex);
      if (lex.length && !qsLectureId) setQsLectureId(String(lex[0].id));
    } catch (e) {
      setErr(e.message);
    }
  }

  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleImport(files) {
    setImporting(true);
    setImportMsg("Importing...");
    try {
      const fd = new FormData();
      for (const f of files) fd.append("files", f);
      const res = await fetch("/api/lectures/import", { method: "POST", body: fd }).then((r) => r.json());
      let msg = `Imported ${res.imported.length} lecture(s).`;
      if (res.imported.length) msg += " OCR, captions, and summaries are generating in the background.";
      if (res.duplicates?.length) msg += ` Skipped ${res.duplicates.length} duplicate(s).`;
      if (res.errors?.length) msg += ` Errors: ${res.errors.join("; ")}`;
      setImportMsg(msg);
      load();
    } catch (e) {
      setImportMsg("Import failed: " + e.message);
    } finally {
      setImporting(false);
    }
  }

  async function handleQsImport(files) {
    if (!qsLectureId) return setQsMsg("Import a lecture first");
    setQsMsg("Parsing questions...");
    try {
      const fd = new FormData();
      fd.append("lecture_id", qsLectureId);
      for (const f of files) fd.append("files", f);
      const res = await fetch("/api/question_sets/import", { method: "POST", body: fd }).then((r) => r.json());
      let msg = `Imported ${res.imported} professor question(s).`;
      if (res.skipped_duplicates) msg += ` Skipped ${res.skipped_duplicates} already imported.`;
      if (res.errors?.length) msg += ` Errors: ${res.errors.join("; ")}`;
      setQsMsg(msg);
      load();
    } catch (e) {
      setQsMsg("Import failed: " + e.message);
    }
  }

  if (err) return <div className="muted">Failed to load dashboard: {err}</div>;
  if (!d) return <Spinner />;

  const weak = d.weak_slides || [];
  const gaps = d.gaps || [];

  return (
    <div className="dash">
      <div className="card stat-strip">
        <Stat label="Lectures" value={d.lecture_count} />
        <Stat label="Coverage" value={d.coverage.question_coverage + "%"} />
        <Stat label="Accuracy" value={pct(d.overall_accuracy)} cls={accClass(d.overall_accuracy)} />
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <Projection p={proj} />
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div style={{ display: "flex", justifyContent: "center" }}>
          <Pills options={UPLOAD_MODES} value={uploadMode} onChange={setUploadMode} />
        </div>
        <div style={{ marginTop: 14 }}>
          {uploadMode === "lectures" ? (
            <>
              <div className="muted upload-hint">
                Imports the slide deck itself (PDF/PPTX) — this is where questions and coverage come from.
              </div>
              <Dropzone label="Drop PDF/PPTX lecture files here, or click to browse" accept=".pdf,.pptx" disabled={importing} onFiles={handleImport} />
              {importMsg && <div className="muted" style={{ marginTop: 8, fontSize: 13 }}>{importMsg}</div>}
            </>
          ) : (
            <div className="narrow-col">
              <select value={qsLectureId} onChange={(e) => setQsLectureId(e.target.value)} style={{ marginBottom: 10 }}>
                {lectures.length
                  ? lectures.map((l) => <option key={l.id} value={l.id}>{l.title}</option>)
                  : <option value="">Import a lecture first</option>}
              </select>
              <div className="muted upload-hint">
                Imports a professor-provided answer key or practice sheet for the lecture above, so those
                exact questions get folded into your drills alongside the AI-generated ones.
              </div>
              <Dropzone label="Drop a question handout here" accept=".pdf,.pptx" disabled={!qsLectureId} onFiles={handleQsImport} />
              {qsMsg && <div className="muted" style={{ marginTop: 8, fontSize: 13 }}>{qsMsg}</div>}
            </div>
          )}
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3>Lecture coverage</h3>
        <table className="dash-table">
          <tbody>
            {d.lectures.map((l) => (
              <tr key={l.lecture_id}>
                <td className="td-title">{l.lecture_title}</td>
                <td className="muted">{l.slides_covered}/{l.slides_total} covered</td>
                <td>{l.q_count}</td>
                <td><span className={`acc-badge ${accClass(l.accuracy)}`}>{pct(l.accuracy)}</span></td>
                <td>
                  <span className="mini-bar"><span className="mini-fill" style={{ width: `${l.coverage_pct}%` }} /></span>
                  <span className="muted"> {l.coverage_pct}%</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="two-col" style={{ marginTop: 16 }}>
        <div className="card">
          <h3>Weak slides</h3>
          {weak.length ? (
            <>
              <ul className="detail-list">
                {weak.slice(0, weakShown).map((s) => (
                  <li key={s.slide_id} className="thumb-row">
                    {s.thumb && <img className="slide-thumb" src={s.thumb} alt="" />}
                    <div>
                      <span className={`acc-badge ${accClass(s.accuracy)}`}>{pct(s.accuracy)}</span>{" "}
                      Slide {s.slide_num}: {shortLabel(s.text)}
                      <div className="muted" style={{ fontSize: 12.5 }}>{s.lecture_title} · {s.answers} answer{s.answers === 1 ? "" : "s"}</div>
                    </div>
                  </li>
                ))}
              </ul>
              {weakShown < weak.length && (
                <button className="btn ghost sm" style={{ marginTop: 10 }} onClick={() => setWeakShown((n) => n + PAGE)}>
                  Show more
                </button>
              )}
            </>
          ) : <div className="muted">Nothing to show.</div>}
        </div>
        <div className="card">
          <h3>Coverage gaps</h3>
          {gaps.length ? (
            <>
              <ul className="detail-list">
                {gaps.slice(0, gapsShown).map((g) => (
                  <li key={g.slide_id} className="thumb-row">
                    {g.thumb && <img className="slide-thumb" src={g.thumb} alt="" />}
                    <div>
                      Slide {g.slide_num}: {shortLabel(g.text)}
                      <div className="muted" style={{ fontSize: 12.5 }}>{g.lecture_title}</div>
                    </div>
                  </li>
                ))}
              </ul>
              {gapsShown < gaps.length && (
                <button className="btn ghost sm" style={{ marginTop: 10 }} onClick={() => setGapsShown((n) => n + PAGE)}>
                  Show more
                </button>
              )}
            </>
          ) : <div className="muted">Nothing to show.</div>}
        </div>
      </div>
    </div>
  );
}
