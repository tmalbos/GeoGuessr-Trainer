import { useEffect, useRef, useState } from "react";

export default function MultiSelectDropdown({ options, selected, onChange, placeholder }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const onClick = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const toggle = (value) => {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value]);
  };

  const label =
    selected.length === options.length ? "All" :
    selected.length === 0 ? placeholder :
    options.filter((o) => selected.includes(o.value)).map((o) => o.label).join(", ");

  return <div className="msdd" ref={ref}>
    <button type="button" className="msdd-btn" onClick={() => setOpen((o) => !o)}>
      <span>{label}</span><span className="msdd-caret">{open ? "▲" : "▼"}</span>
    </button>
    {open && <div className="msdd-panel">
      {options.map((o) => <label className="msdd-option" key={o.value}>
        <input type="checkbox" checked={selected.includes(o.value)} onChange={() => toggle(o.value)} />
        {o.label}
      </label>)}
    </div>}
  </div>;
}
