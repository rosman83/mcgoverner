import { useRef, useState } from "react";
import { UploadIcon } from "./icons";

// Drag-and-drop file picker - replaces the old plain <input type=file> + button.
export function Dropzone({ label, accept, multiple = true, onFiles, disabled }) {
  const [active, setActive] = useState(false);
  const inputRef = useRef(null);

  const handleFiles = (fileList) => {
    const files = Array.from(fileList || []);
    if (files.length) onFiles(files);
  };

  return (
    <div
      className={`dropzone${active ? " active" : ""}`}
      onClick={() => !disabled && inputRef.current?.click()}
      onDragOver={(e) => { e.preventDefault(); setActive(true); }}
      onDragLeave={() => setActive(false)}
      onDrop={(e) => {
        e.preventDefault();
        setActive(false);
        if (!disabled) handleFiles(e.dataTransfer.files);
      }}
    >
      <UploadIcon style={{ margin: "0 auto 8px", display: "block", color: "var(--muted)" }} />
      {label}
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        onChange={(e) => { handleFiles(e.target.files); e.target.value = ""; }}
      />
    </div>
  );
}
