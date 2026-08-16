// Use sparingly - only when a number/label genuinely needs explaining, not as
// a default way to add paragraphs of copy to the UI.
export function InfoPopup({ children }) {
  return (
    <span className="info-trigger">
      i
      <span className="info-popup">{children}</span>
    </span>
  );
}
