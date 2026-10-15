import { useCallback, useEffect, useRef, useState } from "react";
import checkpointUrl from "../../assets/sprites/add-checkpoint.svg";
import compassUrl from "../../assets/sprites/compass.svg";
import returnUrl from "../../assets/sprites/return-to-start.svg";
import ErrorBox from "../../components/ErrorBox.jsx";
import { api, errorText } from "../../lib/api.js";
import { num } from "../../lib/format.js";
import { coordsText } from "../../lib/guess.js";
import { fetchPlace } from "../../lib/place.js";
import { createStreetView } from "../../lib/streetView.js";
import Minimap from "./Minimap.jsx";
import "./minigames.css";

// First-person 3D walk over the road network of a random real place (flat, no buildings yet).
// The round starts at a node; the player walks around, then pins where they think that start was.

function Scene({ place, onNode, onReady, viewRef }) {
  const box = useRef(null), needle = useRef(null);
  useEffect(() => {
    const view = createStreetView(box.current, place, {
      onNode,
      onView: (h) => { if (needle.current) needle.current.style.transform = `rotate(${(-h * 180) / Math.PI}deg)`; },
    });
    viewRef.current = view;
    onReady(view.empty ? null : view.start);
    return () => { view.dispose(); viewRef.current = null; };
  }, [place, onNode, onReady, viewRef]);
  return <>
    <div ref={box} style={{ position: "absolute", inset: 0 }} />
    <div className="sv-compass" aria-hidden="true"><img ref={needle} className="sv-needle" src={compassUrl} alt="" draggable={false} /></div>
    <span className="mg-hint">Drag to look · click an arrow or W / S to move · hold Space to keep going · A / D to turn</span>
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
  const [truth, setTruth] = useState(null);   // where the round started (the answer), local metres
  const [result, setResult] = useState(null); // set once the player guessed
  const [checkpoint, setCheckpoint] = useState(null);
  const [round, setRound] = useState(0);
  const abort = useRef(null);
  const viewRef = useRef(null);

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
      setHud(null); setTruth(null); setResult(null); setCheckpoint(null); setRound((n) => n + 1); setPlace(p);
    } catch (e) {
      if (e.name !== "AbortError") setErr(errorText(e));
    } finally { if (abort.current === ac) setLoading(false); }
  };

  const onNode = useCallback((n) => setHud(n), []);
  const onReady = useCallback((s) => setTruth(s), []);
  const act = (fn) => (e) => { e.currentTarget.blur(); fn(viewRef.current); };

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
        <h3>{result ? "This place" : "Round"}</h3>
        <dl className="mg-facts">
          {result && truth && <><dt>Country</dt><dd>{place.meta.country}</dd>
            <dt>Start point</dt><dd>{coordsText(place.meta, truth.x, truth.y)}</dd></>}
          {hud && <><dt>Steps taken</dt><dd>{num(hud.steps)}</dd>
            <dt>Walkable points</dt><dd>{num(hud.nodes)}</dd></>}
        </dl>
        <h3>Controls</h3>
        <dl className="mg-facts">
          <dt>Look</dt><dd>Drag</dd>
          <dt>Move</dt><dd>Click arrow · W / S</dd>
          <dt>Keep going</dt><dd>Hold Space</dd>
          <dt>Turn</dt><dd>A / D</dd>
          <dt>Zoom</dt><dd>Scroll</dd>
          <dt>Guess</dt><dd>Pin on the minimap</dd>
        </dl>
      </>}
    </aside>

    <div className="mg-view">
      {err && <ErrorBox message={err} onRetry={roll} />}
      <div className={`mg-canvas-wrap ${loading ? "busy" : ""}`}>
        {place ? <>
          <Scene place={place} onNode={onNode} onReady={onReady} viewRef={viewRef} />
          <div className="sv-actions">
            <button type="button" className="sv-act" title="Return to start" aria-label="Return to start" onClick={act((v) => v?.teleport(v.startNode))}><img src={returnUrl} alt="" /></button>
            <button type="button" className="sv-act" title="Add checkpoint here" aria-label="Add checkpoint here" onClick={act((v) => v && setCheckpoint(v.current()))}><img src={checkpointUrl} alt="" /></button>
            {checkpoint != null && <button type="button" className="sv-act cp" title="Return to checkpoint" aria-label="Return to checkpoint" onClick={act((v) => v?.teleport(checkpoint))}><img src={returnUrl} alt="" /></button>}
          </div>
          {truth && <Minimap key={round} place={place} truth={truth} result={result} onGuess={setResult} onNext={roll} busy={loading} />}
        </> : <div className="mg-empty">Press “Random place” to start walking.</div>}
      </div>
    </div>
  </div>;
}
