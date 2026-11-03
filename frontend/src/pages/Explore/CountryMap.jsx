import { useEffect, useState } from "react";
import { loadCountryOutline } from "./geo.js";

const DOT_R = 11; // marker radius in map units (the map is about 1000 units on its longest side)

// The country's national outline: one shape, no first-level subdivisions.
export default function CountryMap({ id }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let off = false;
    setData(null); setErr("");
    loadCountryOutline(id).then((d) => !off && setData(d)).catch((e) => !off && setErr(e.message));
    return () => { off = true; };
  }, [id]);

  if (err) return <div className="cm"><p className="err">{err}</p></div>;
  if (!data) return <div className="cm"><p className="muted">Loading map…</p></div>;

  const pad = 8;
  return <div className="cm">
    <svg className="cm-svg" viewBox={`${-pad} ${-pad} ${data.w + pad * 2} ${data.h + pad * 2}`} role="img"
      aria-label={`Outline of ${data.name}`}>
      <path className="cm-nat" d={data.d} fillRule="evenodd" />
      {data.dots.map(([x, y], i) => <circle key={i} className="cm-dot" cx={x} cy={y} r={DOT_R} />)}
    </svg>
  </div>;
}
