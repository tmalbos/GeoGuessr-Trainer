import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import StatusIcon from "../../../components/StatusIcon.jsx";
import { useToast } from "../../../components/Toast.jsx";
import {
  autoCropRect, cropCanvas, discardStaged, loadImage,
  stageBlob, stageFile, stagePath, stagedUrl, toBlob, toCanvas,
} from "./imageTools.js";
import { isPathLike, kindOfFile, kindOfName } from "./mediaKinds.js";
import "./editor.css";

const MIN_H = 120; // smallest height limit the handle can set (px)
const ANY_ACCEPT = "image/*,.svg,.csv,.geojson,.json,.gpkg,.zip,.parquet,.kml";
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

// ── icons (the whole image column is icon-only; names live in tooltips / aria-labels) ──
const I = (p) => <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
  strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...p} />;
const Crop = () => <I><path d="M6 2v14a2 2 0 0 0 2 2h14" /><path d="M18 22V8a2 2 0 0 0-2-2H2" /></I>;
const Wand = () => <I><path d="M15 4V2M15 16v-2M8 9h2M20 9h2M17.8 11.8 19 13M17.8 6.2 19 5M12.2 6.2 11 5" /><path d="m3 21 9-9" /></I>;
const Undo = () => <I><path d="M9 14 4 9l5-5" /><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11" /></I>;
const Eye = ({ off }) => <I><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" />{off && <path d="M3 3l18 18" />}</I>;
const Upload = () => <I><path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 16v4h16v-4" /></I>;
const Check = () => <I><path d="M5 12.5l4.5 4.5L19 7.5" /></I>;
const X = () => <I><path d="M6 6l12 12M18 6 6 18" /></I>;
const Unlimit = () => <I><path d="M12 3v18M8 7l4-4 4 4M8 17l4 4 4-4" /></I>;
const Trash = () => <I><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /><path d="M10 11v6M14 11v6" /></I>;
const Pic = () => <I width="44" height="44" strokeWidth="1.5"><rect x="3" y="4" width="18" height="16" rx="2" /><circle cx="9" cy="10" r="1.6" /><path d="m21 16-5-5-8 8" /></I>;

const Tool = ({ icon, label, cls = "", ...p }) =>
  <button type="button" className={`ed-tool ${cls}`} aria-label={label} title={label} {...p}>{icon}</button>;

// Crop box handles: [id, x, y, cursor]
const HANDLES = [
  ["nw", 0, 0, "nwse-resize"], ["n", 0.5, 0, "ns-resize"], ["ne", 1, 0, "nesw-resize"], ["e", 1, 0.5, "ew-resize"],
  ["se", 1, 1, "nwse-resize"], ["s", 0.5, 1, "ns-resize"], ["sw", 0, 1, "nesw-resize"], ["w", 0, 0.5, "ew-resize"],
];

// A press this many pixels from a side (or corner) of the crop box resizes it; anything further
// inside moves the whole box. Wide enough that the edges are easy to grab, on the handles or not.
const EDGE_PX = 12;

/** Which drag starts at (px, py) — pixels inside the crop area. "w"/"e"/"n"/"s" (or two of them for a
 *  corner) resize, "move" moves the box, "" does nothing. */
function hitKind(s, px, py, w, h) {
  const l = s.x * w, r = (s.x + s.w) * w, t = s.y * h, b = (s.y + s.h) * h;
  const inX = px >= l - EDGE_PX && px <= r + EDGE_PX;
  const inY = py >= t - EDGE_PX && py <= b + EDGE_PX;
  const kx = !inY ? "" : px <= l + EDGE_PX ? "w" : px >= r - EDGE_PX ? "e" : "";
  const ky = !inX ? "" : py <= t + EDGE_PX ? "n" : py >= b - EDGE_PX ? "s" : "";
  return kx + ky || (inX && inY ? "move" : "");
}

const cursorOf = (k) =>
  k === "move" ? "move"
    : k.length === 2 ? (k === "wn" || k === "es" ? "nwse-resize" : "nesw-resize")
      : k === "n" || k === "s" ? "ns-resize" : k ? "ew-resize" : "";

/** s = start rect in fractions (0..1) of the image; dx, dy = pointer movement in the same fractions. */
function dragCrop(kind, s, dx, dy, min) {
  if (kind === "move") return { ...s, x: clamp(s.x + dx, 0, 1 - s.w), y: clamp(s.y + dy, 0, 1 - s.h) };
  let x0 = s.x, y0 = s.y, x1 = s.x + s.w, y1 = s.y + s.h;
  if (kind.includes("w")) x0 = clamp(x0 + dx, 0, x1 - min.w);
  if (kind.includes("e")) x1 = clamp(x1 + dx, x0 + min.w, 1);
  if (kind.includes("n")) y0 = clamp(y0 + dy, 0, y1 - min.h);
  if (kind.includes("s")) y1 = clamp(y1 + dy, y0 + min.h, 1);
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/** The left column of the clue editor: shows the image exactly like the card does, plus its tools.
 *  Controlled: blur / maxH live in the editor. Edits are kept in memory (undo works) and only uploaded on save:
 *  ref.resolve() -> staging token of the image to save, or null when the clue's current image is untouched.
 *  onState(has, sig): has = there is an image; sig changes whenever the image does (dirty tracking).
 *  `preview` (SVG / CSV / geometry) takes the picture's place; `incoming` is a staged image to adopt;
 *  anything that is not an image is handed to onOtherFile / onOtherText. */
const ClueImage = forwardRef(function ClueImage({ current, blur, onBlur, maxH, onMaxH, colRef, onState, overlay: badges,
  preview, incoming, onOtherFile, onOtherText, onRemove }, ref) {
  const toast = useToast();
  const [src, setSrc] = useState(null);      // { token, url }: a staged upload that replaces `current`
  const [hist, setHist] = useState([]);      // canvases from before each edit (undo stack)
  const [editedUrl, setEditedUrl] = useState(null);
  const [mode, setMode] = useState("view");  // view | crop
  const [crop, setCrop] = useState(null);    // { x, y, w, h } as fractions of the image
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [over, setOver] = useState(false);
  const [path, setPath] = useState("");
  const tried = useRef("");
  const [hh, setHh] = useState(null);        // live height readout while dragging the handle
  const work = useRef(null);                 // current pixels, once the first edit happens
  const blobUrl = useRef(null);
  const img = useRef(null);
  const overlay = useRef(null);
  const picker = useRef(null);
  const drag = useRef(null);
  const hdrag = useRef(null);
  const live = useRef({});
  live.current = { src, hist };

  const displayUrl = editedUrl ?? src?.url ?? current ?? null;
  const custom = preview != null; // the media is an SVG / CSV / geometry, drawn by the editor
  const has = custom || !!displayUrl;
  const limited = !custom && has && !!maxH;

  useEffect(() => { onState?.(has, `${custom ? "x" : src?.token ?? ""}:${hist.length}`); }, [has, custom, src, hist.length]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => { if (blobUrl.current) URL.revokeObjectURL(blobUrl.current); }, []);

  // A staged image from outside (a Street View shot, or a file dropped on a media slot showing another kind).
  useEffect(() => {
    if (!incoming?.token) return;
    discardStaged(live.current.src?.token);
    work.current = null; setHist([]); showUrl(null);
    setSrc({ token: incoming.token, url: stagedUrl(incoming.token) });
    setMode("view");
  }, [incoming]); // eslint-disable-line react-hooks/exhaustive-deps

  useImperativeHandle(ref, () => ({
    async resolve() {
      const { src: s, hist: h } = live.current;
      if (h.length) {
        const r = await stageBlob(await toBlob(work.current));
        discardStaged(s?.token);
        return r.token;
      }
      return s?.token ?? null;
    },
  }), []);

  // ── pixels ──
  const showUrl = (u) => {
    if (blobUrl.current) URL.revokeObjectURL(blobUrl.current);
    blobUrl.current = u;
    setEditedUrl(u);
  };
  const getWork = async () => {
    if (!work.current) work.current = toCanvas(await loadImage(live.current.src?.url ?? current));
    return work.current;
  };
  const commit = async (next) => {
    const prev = work.current;
    showUrl(URL.createObjectURL(await toBlob(next)));
    work.current = next;
    setHist((h) => [...h, prev]);
  };
  const run = async (fn) => {
    setBusy(true); setErr("");
    try {
      const next = await fn(await getWork());
      if (next) await commit(next);
    } catch (e) { setErr(e.message || "Could not edit the image"); } finally { setBusy(false); }
  };
  const undo = async () => {
    const h = live.current.hist;
    if (!h.length) return;
    const rest = h.slice(0, -1);
    setHist(rest);
    if (!rest.length) { work.current = null; showUrl(null); return; }
    work.current = h[h.length - 1];
    try { showUrl(URL.createObjectURL(await toBlob(work.current))); } catch (e) { setErr(e.message); }
  };

  const autoCrop = () => run((c) => {
    const r = autoCropRect(c);
    if (!r) { toast.push("Nothing to trim", { ms: 2200 }); return null; }
    return cropCanvas(c, r);
  });
  const startCrop = () => { setCrop({ x: 0.06, y: 0.06, w: 0.88, h: 0.88 }); setMode("crop"); };
  const cancelCrop = () => setMode("view");
  const confirmCrop = async () => {
    const c = crop;
    setMode("view");
    if (!c || (c.w > 0.99 && c.h > 0.99)) return;
    await run((w) => cropCanvas(w, { x: c.x * w.width, y: c.y * w.height, w: c.w * w.width, h: c.h * w.height }));
  };

  // Enter applies the crop, Escape drops it (and does not close the editor behind it).
  useEffect(() => {
    if (mode !== "crop") return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); cancelCrop(); }
      else if (e.key === "Enter" && !/^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(e.target?.tagName ?? "")) { e.preventDefault(); confirmCrop(); }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }); // re-bound every render so it always sees the latest crop

  // ── new file / path ──
  const replaceWith = async (job) => {
    setBusy(true); setErr("");
    try {
      const r = await job();
      discardStaged(live.current.src?.token);
      work.current = null; setHist([]); showUrl(null);
      setSrc({ token: r.token, url: stagedUrl(r.token) });
      setMode("view");
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const pickFile = (f) => {
    if (!f) return;
    if (!custom && kindOfFile(f) === "image") replaceWith(() => stageFile(f));
    else onOtherFile?.(f);
  };
  const pickPath = (p) => {
    const clean = p.trim().replace(/^"|"$/g, "");
    if (!clean) return;
    tried.current = clean; setPath(clean);
    // A link, or a file that is not an image, belongs to the editor (SVG / CSV / geometry / Street View).
    if (custom || !isPathLike(clean) || (kindOfName(clean) && kindOfName(clean) !== "image")) { onOtherText?.(clean); return; }
    replaceWith(() => stagePath(clean));
  };
  const pickRef = useRef({});
  pickRef.current = { pickFile, pickPath };

  // Ctrl+V anywhere in the editor (outside text fields): an image, a file path or a Street View link.
  useEffect(() => {
    const onPaste = (e) => {
      if (/^(INPUT|TEXTAREA)$/.test(e.target?.tagName ?? "")) return;
      const f = e.clipboardData?.files?.[0];
      if (f) { e.preventDefault(); pickRef.current.pickFile(f); return; }
      const t = (e.clipboardData?.getData("text") ?? "").trim();
      if (isPathLike(t) || /^https?:\/\//i.test(t)) { e.preventDefault(); pickRef.current.pickPath(t); }
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, []);

  const dropProps = {
    onDragOver: (e) => { e.preventDefault(); setOver(true); },
    onDragLeave: () => setOver(false),
    onDrop: (e) => { e.preventDefault(); setOver(false); pickFile(e.dataTransfer.files[0]); },
  };

  // ── crop box pointer handling ──
  // Bound to the whole crop area, not to the little handles: a press anywhere near a side resizes it,
  // anywhere inside it moves it. Pressing a handle exactly used to be the only way to resize, and a
  // near miss grabbed the box body instead — which can only slide `1 - w` to the right, so it looked
  // like the left edge refused to move ("it jumps to that crop").
  const cropDown = (e) => {
    if (e.button || !crop) return;
    const r = overlay.current.getBoundingClientRect();
    const kind = hitKind(crop, e.clientX - r.left, e.clientY - r.top, r.width, r.height);
    if (!kind) return;
    e.preventDefault(); e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { kind, x: e.clientX, y: e.clientY, s: crop };
  };
  const cropMove = (e) => {
    if (!crop) return;
    const r = overlay.current.getBoundingClientRect();
    const d = drag.current;
    if (!d) {
      e.currentTarget.style.cursor = cursorOf(hitKind(crop, e.clientX - r.left, e.clientY - r.top, r.width, r.height));
      return;
    }
    e.stopPropagation();
    setCrop(dragCrop(d.kind, d.s, (e.clientX - d.x) / r.width, (e.clientY - d.y) / r.height, { w: 24 / r.width, h: 24 / r.height }));
  };
  const cropUp = (e) => { if (drag.current) e.stopPropagation(); drag.current = null; };

  // ── height limit handle ──
  const fullHeight = () => {
    const i = img.current;
    const w = colRef.current?.clientWidth || 560;
    return i?.naturalWidth ? (w * i.naturalHeight) / i.naturalWidth : 9999;
  };
  const setLimit = (h) => { const full = fullHeight(); onMaxH(h >= full - 4 ? null : Math.round(h)); };
  const hDown = (e) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    hdrag.current = { y: e.clientY, h: img.current.clientHeight };
  };
  const hMove = (e) => {
    const d = hdrag.current;
    if (!d) return;
    const h = clamp(Math.round(d.h + e.clientY - d.y), MIN_H, Math.round(fullHeight()));
    setHh(h);
    setLimit(h);
  };
  const hUp = () => { hdrag.current = null; setHh(null); };
  const hKey = (e) => {
    if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
    e.preventDefault();
    const full = Math.round(fullHeight());
    setLimit(clamp((maxH ?? full) + (e.key === "ArrowUp" ? -20 : 20), MIN_H, full));
  };

  const off = busy;
  const remove = () => {
    discardStaged(live.current.src?.token);
    work.current = null; setHist([]); showUrl(null); setSrc(null); setPath(""); setMode("view");
    onRemove?.();
  };
  return <>
    {has ? <div className={`clue-media ed-media ${over ? "over" : ""} ${limited ? "limited" : ""} ${mode === "crop" ? "cropping" : ""}`}
        style={limited ? { "--mh": `${maxH}px` } : undefined} {...dropProps}>
      {custom ? preview : <img ref={img} src={displayUrl} alt="Clue" draggable={false} className={blur && mode === "view" ? "clue-blur" : undefined} />}
      {badges}
      {busy && <div className="ed-busy"><StatusIcon kind="wait" /></div>}
      {!custom && mode === "crop" && crop && <div className="ed-crop" ref={overlay}
        onPointerDown={cropDown} onPointerMove={cropMove} onPointerUp={cropUp} onPointerCancel={cropUp}>
        <div className="ed-sel" style={{ left: `${crop.x * 100}%`, top: `${crop.y * 100}%`, width: `${crop.w * 100}%`, height: `${crop.h * 100}%` }}
          onDoubleClick={confirmCrop}>
          {HANDLES.map(([id, x, y, cursor]) =>
            <i key={id} className="ed-h" style={{ left: `${x * 100}%`, top: `${y * 100}%`, cursor }} />)}
        </div>
      </div>}
      {!custom && mode === "view" && !busy && <>
        {hh != null && <span className="ed-hval">{hh} px</span>}
        <div className={`ed-hh ${limited ? "set" : ""} ${hh != null ? "on" : ""}`} role="slider" tabIndex={0} aria-orientation="vertical"
          aria-label="Maximum image height" aria-valuemin={MIN_H} aria-valuenow={maxH ?? Math.round(fullHeight())} title="Drag to limit the height"
          onPointerDown={hDown} onPointerMove={hMove} onPointerUp={hUp} onKeyDown={hKey} onDoubleClick={() => onMaxH(null)} />
      </>}
    </div> : <div className={`ed-drop ${over ? "over" : ""}`} role="button" tabIndex={0}
        aria-label="Add an image, SVG, CSV, geometry file or Street View link" {...dropProps}
        onClick={() => picker.current.click()}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); picker.current.click(); } }}>
      {busy ? <StatusIcon kind="wait" /> : <Pic />}
      {badges}
    </div>}

    <div className="ed-tools" role="toolbar" aria-label="Media tools">
      {!custom && mode === "crop" ? <>
        <Tool icon={<Check />} label="Apply crop" cls="ok" onClick={confirmCrop} />
        <Tool icon={<X />} label="Cancel crop" cls="no" onClick={cancelCrop} />
      </> : <>
        {!custom && has && <>
          <Tool icon={<Crop />} label="Crop" disabled={off} onClick={startCrop} />
          <Tool icon={<Wand />} label="Auto-crop" disabled={off} onClick={autoCrop} />
          <Tool icon={<Undo />} label="Undo" disabled={off || !hist.length} onClick={undo} />
          <i className="ed-sep" />
          <Tool icon={<Eye off={blur} />} label={blur ? "Blurred until hover" : "Blur until hover"} aria-pressed={blur} onClick={() => onBlur(!blur)} />
          {limited && <Tool icon={<Unlimit />} label="Remove height limit" onClick={() => onMaxH(null)} />}
          <i className="ed-sep" />
        </>}
        {has && <Tool icon={<Trash />} label="Remove media" disabled={off} onClick={remove} />}
        <input className="ed-path" value={path} disabled={busy} aria-label="File path or Street View link"
          placeholder="Type or paste a file path or a Street View link"
          onChange={(e) => setPath(e.target.value)}
          onPaste={(e) => {
            const f = e.clipboardData.files[0];
            if (f) { e.preventDefault(); pickFile(f); return; }
            const t = e.clipboardData.getData("text");
            if (t.trim()) { e.preventDefault(); pickPath(t); }
          }}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); pickPath(path); } }}
          onBlur={() => { if (path.trim() && path.trim() !== tried.current && !busy) pickPath(path); }} />
        <Tool icon={<Upload />} label={has ? "Replace media" : "Choose a file"} disabled={off} onClick={() => picker.current.click()} />
      </>}
    </div>
    {err && <p className="err">{err}</p>}
    <input ref={picker} type="file" accept={ANY_ACCEPT} hidden onChange={(e) => { pickFile(e.target.files[0]); e.target.value = ""; }} />
  </>;
});

export default ClueImage;
