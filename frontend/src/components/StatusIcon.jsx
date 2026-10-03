const p = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };

/** ok = check, bad = cross, warn = triangle (optional/degraded), wait = spinner. Shape + color, never color alone. */
export default function StatusIcon({ kind }) {
  const label = { ok: "OK", bad: "Problem", warn: "Optional", wait: "Loading" }[kind];
  return <span className={`st-icon ${kind}`} role="img" aria-label={label}>
    {kind === "ok" && <svg {...p}><circle cx="12" cy="12" r="9" /><path d="M8 12.5l3 3 5-6" /></svg>}
    {kind === "bad" && <svg {...p}><circle cx="12" cy="12" r="9" /><path d="M9 9l6 6M15 9l-6 6" /></svg>}
    {kind === "warn" && <svg {...p}><path d="M12 3l10 18H2z" /><path d="M12 10v5M12 18v.5" /></svg>}
    {kind === "wait" && <svg {...p} className="spin"><path d="M12 3a9 9 0 1 0 9 9" /></svg>}
  </span>;
}
