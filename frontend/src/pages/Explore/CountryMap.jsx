import { useEffect, useState } from "react";
import { loadCountryOutline } from "./geo.js";

const DOT_R = 11; // marker radius in map units (the map is about 1000 units on its longest side)

// The country's national outline: one shape, no first-level subdivisions.
// `fit` = [maxWidth, maxHeight] in px: the drawing is sized to its own proportions inside that box,
// so there is no empty margin around it (a plain fixed-size box left gaps on the sides / top).
export default function CountryMap({ id, fit }) {
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
  const vw = data.w + pad * 2;
  const vh = data.h + pad * 2;
  let style;
  if (fit) {
    const s = Math.min(fit[0] / vw, fit[1] / vh);
    style = { width: Math.round(vw * s), height: Math.round(vh * s) };
  }
  return <div className="cm">
    <svg className="cm-svg" style={style} viewBox={`${-pad} ${-pad} ${vw} ${vh}`} role="img"
      aria-label={`Outline of ${data.name}`}>
      <path className="cm-nat" d={data.d} fillRule="evenodd" />
      {data.dots.map(([x, y], i) => <circle key={i} className="cm-dot" cx={x} cy={y} r={DOT_R} />)}
    </svg>
  </div>;
}
