import { useCallback, useEffect, useRef, useState } from "react";
import ErrorBox from "../../components/ErrorBox.jsx";
import { api, errorText } from "../../lib/api.js";
import { num } from "../../lib/format.js";
import { fetchPlace } from "../../lib/place.js";
import { createStreetView } from "../../lib/streetView.js";
import "./minigames.css";

// First-person 3D walk over the road network of a random real place (flat, no buildings yet).

const M_PER_DEG = 111320;
const coords = (meta, x, y) => {
  const lat = meta.lat + y / M_PER_DEG;
  const lon = meta.lon + x / (M_PER_DEG * Math.cos((lat * Math.PI) / 180));
  return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
};

function Scene({ place, onNode }) {
  const box = useRef(null), needle = useRef(null);
  useEffect(() => {
    const view = createStreetView(box.current, place, {
      onNode,
      onView: (h) => { if (needle.current) needle.current.style.transform = `rotate(${(-h * 180) / Math.PI}deg)`; },
    });
    return () => view.dispose();
  }, [place, onNode]);
  return <>
    <div ref={box} style={{ position: "absolute", inset: 0 }} />
    <div className="sv-compass" aria-hidden="true"><div ref={needle} className="sv-needle"><b>N</b></div></div>
    <span className="mg-hint">Drag to look · click an arrow or W / S to move · A / D to turn · scroll to zoom</span>
  </>;
}

export default function StreetView() {
  const [info, setInfo] = useState(null);
  const [infoErr, setInfoErr] = useState("");
  const [si, setSi] = useState(1);
  const [place, setPlace] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [hud, setHud] = useState(null);
  const abort = useRef(null);

  const loadInfo = useCallback(() => {
    setInfoErr("");
    return api("/minigames/countries").then(setInfo).catch((e) => setInfoErr(errorText(e)));
  }, []);
  useEffect(() => { loadInfo(); return () => abort.current?.abort(); }, [loadInfo]);

  const sizes = info?.sizes ?? [];
  const sizeKm = sizes[Math.min(si, sizes.length - 1)];
  const ready = info?.countries.filter((c) => c.valid[sizeKm] > 0).length ?? 0;

  const roll = async () => {
    abort.current?.abort();
    const ac = (abort.current = new AbortController());
    setLoading(true); setErr("");
    try {
      const p = await fetchPlace(sizeKm, null, ac.signal);
      setHud(null); setPlace(p);
    } catch (e) {
      if (e.name !== "AbortError") setErr(errorText(e));
    } finally { if (abort.current === ac) setLoading(false); }
  };

  const onNode = useCallback((n) => setHud(n), []);

  if (infoErr) return <ErrorBox message={infoErr} onRetry={loadInfo} />;

  return <div className="mg">
    <aside className="mg-panel">
      <button type="button" className="btn mg-go" onClick={roll} disabled={loading || !ready}>{loading ? "Finding a place…" : "Random place"}</button>
      {info && info.countries.length > 0 && <>
        <div className="mg-row">
          <label htmlFor="sv-size">Area size<output>{sizeKm} × {sizeKm} km</output></label>
          <input id="sv-size" type="range" min="0" max={sizes.length - 1} step="1" value={si} onChange={(e) => setSi(Number(e.target.value))} />
          <div className="mg-ends"><span>Small town</span><span>Big city</span></div>
        </div>
        <p className="muted mg-note">Smaller areas load faster. Each road is walkable every 12 m.</p>
      </>}
      {info && !info.countries.length && <p className="muted mg-note">No maps are ready yet. Build them with <code>python -m scripts.build_minigame_gpkg</code>.</p>}

      {place && <>
        <h3>This place</h3>
        <dl className="mg-facts">
          <dt>Country</dt><dd>{place.meta.country}</dd>
          {hud && <><dt>Position</dt><dd>{coords(place.meta, hud.x, hud.y)}</dd>
            <dt>Steps taken</dt><dd>{num(hud.steps)}</dd>
            <dt>Walkable points</dt><dd>{num(hud.nodes)}</dd></>}
        </dl>
        <h3>Controls</h3>
        <dl className="mg-facts">
          <dt>Look</dt><dd>Drag</dd>
          <dt>Move</dt><dd>Click arrow · W / S</dd>
          <dt>Turn</dt><dd>A / D</dd>
          <dt>Zoom</dt><dd>Scroll</dd>
        </dl>
      </>}
    </aside>

    <div className="mg-view">
      {err && <ErrorBox message={err} onRetry={roll} />}
      <div className={`mg-canvas-wrap ${loading ? "busy" : ""}`}>
        {place ? <Scene place={place} onNode={onNode} /> : <div className="mg-empty">Press “Random place” to start walking.</div>}
      </div>
    </div>
  </div>;
}
