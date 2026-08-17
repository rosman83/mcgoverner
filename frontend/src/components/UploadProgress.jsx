// Real progress while bytes are sending, then an indeterminate animated bar
// once the upload finishes and we're waiting on the server (parsing a
// lecture into slides has no progress signal to show).
export function UploadProgress({ state }) {
  if (!state) return null;
  const processing = state.phase === "processing";
  return (
    <div className="upload-progress">
      <div className="upload-progress-bar">
        <div
          className={`upload-progress-fill${processing ? " indeterminate" : ""}`}
          style={processing ? undefined : { width: `${state.pct}%` }}
        />
      </div>
      <div className="muted upload-progress-label">
        {processing ? "Processing…" : `Uploading… ${state.pct}%`}
      </div>
    </div>
  );
}
