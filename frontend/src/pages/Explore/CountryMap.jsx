import { memo, useEffect, useState } from "react";
import { loadCountryMap } from "./geo.js";

const MAX_LISTED = 4;

const DOT_R = 11; // marker radius in map units (the map is about 1000 units on its longest side)

// Island nations: atolls and islets are far too small to see, so each gets a marker dot on top of its shape.
const Regions = memo(function Regions({ regions }) {
  return <>
    {regions.map((r) => <path key={r.name} className="cm-r" d={r.d} data-name={r.name} fillRule="evenodd" />)}
    {regions.flatMap((r) => (r.dots ?? []).map(([x, y], i) =>
      <circle key={`${r.name}-${i}`} className="cm-dot" cx={x} cy={y} r={DOT_R} data-name={r.name} />))}
  </>;
});

// A country with its first-level subdivisions. Hovering a region shows its name underneath.
export default function CountryMap({ id }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [hover, setHover] = useState("");

  useEffect(() => {
    let off = false;
    setData(null); setErr(""); setHover("");
    loadCountryMap(id).then((d) => !off && setData(d)).catch((e) => !off && setErr(e.message));
    return () => { off = true; };
  }, [id]);

  if (err) return <div className="cm"><p className="err">{err}</p></div>;
  if (!data) return <div className="cm"><p className="muted">Loading map…</p></div>;

  const n = data.regions.length;
  const omitted = data.omitted ?? [];
  const pad = 8;
  return <div className="cm">
    <svg className="cm-svg" viewBox={`${-pad} ${-pad} ${data.w + pad * 2} ${data.h + pad * 2}`} role="img"
      aria-label={`Map of ${data.name} with its ${n} first-level subdivisions`}
      onMouseOver={(e) => setHover(e.target.dataset?.name ?? "")} onMouseLeave={() => setHover("")}>
      <Regions regions={data.regions} />
    </svg>
    <div className="cm-name" aria-live="polite">{hover}</div>
    {omitted.length > 0 && <p className="muted cm-omit">
      Not shown: {omitted.slice(0, MAX_LISTED).join(", ")}{omitted.length > MAX_LISTED ? ` and ${omitted.length - MAX_LISTED} more` : ""}
    </p>}
  </div>;
}
