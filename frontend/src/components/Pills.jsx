// Segmented pill switcher - replaces tab bars wherever there are just a
// handful of mutually-exclusive views/filters.
export function Pills({ options, value, onChange }) {
  return (
    <div className="pills">
      {options.map((opt) => (
        <button
          key={opt.value}
          className={`pill${opt.value === value ? " active" : ""}`}
          onClick={() => onChange(opt.value)}
          type="button"
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
