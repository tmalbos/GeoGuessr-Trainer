import { useEffect, useMemo, useRef, useState } from "react";
import ConfirmDialog from "../../../components/ConfirmDialog.jsx";
import Flag from "../../../components/Flag.jsx";
import { api, errorText } from "../../../lib/api.js";
import { streetViewAvailable } from "../../../lib/mapProviders.js";
import AreaPicker from "./AreaPicker.jsx";
import ClueImage from "./ClueImage.jsx";
import ClueMap from "./ClueMap.jsx";
import DrawMap from "./DrawMap.jsx";
import { Preview } from "./FilePicker.jsx";
import RatingDots from "./RatingDots.jsx";
import RelatePicker from "./RelatePicker.jsx";
import RichEditor from "./RichEditor.jsx";
import { MAPMAKING_FILE, autoRatings } from "./autoRatings.js";
import { guessCategory } from "./autoTags.js";
import { useCountries } from "./countries.js";
import fileUrl from "./fileUrl.js";
import { discardStaged, stageFile, stagePath } from "./imageTools.js";
import { kindOfFile, kindOfName, resolveStreetView, streetViewShot } from "./mediaKinds.js";
import { NOTE_TYPES, REL_LABEL, VERBS, joinNames, sameRel, uniq, verbFor } from "./relations.js";
import { LocationIcon } from "./tagUi.jsx";
import { CATEGORY_ICONS, DEFAULT_LOCATION, GENERAL_TAGS, LOCATION_TAGS, RATINGS, tagHue } from "./tags.js";
import "./blur.css";
import "./clues.css";
import "./editor.css";
import "./import-extras.css";
import "./relations.css";

const GEOM_FIELDS = ["polygons", "lines", "points"];
const geoKindOf = (c) => (c ? GEOM_FIELDS.find((k) => c[k]) : undefined);
// A geometry upload says polygons / lines / points; the editor calls all of them "geometry".
const uiKind = (k) => (GEOM_FIELDS.includes(k) ? "geometry" : k);

const initialMedia = (c) => {
  if (!c) return { kind: null, upload: null };
  if (c.image) return { kind: "image", upload: null };
  if (c.svg) return { kind: "svg", upload: null };
  if (c.csv) return { kind: "csv", upload: null };
  return geoKindOf(c) ? { kind: "geometry", upload: null } : { kind: null, upload: null };
};
const initialArea = (c) => {
  if (!c || !c.image) return { kind: null, upload: null };
  if (c.svg) return { kind: "svg", upload: null };
  if (c.csv) return { kind: "csv", upload: null };
  return geoKindOf(c) ? { kind: "geometry", upload: null } : { kind: null, upload: null };
};

const URL_OK = /^https?:\/\/\S+$/i;
const trunc = (s, n) => (s.length > n ? `${s.slice(0, n)}…` : s);

const ico = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
const LinkIcon = () => <svg {...ico}><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" /><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" /></svg>;
const LinkPlus = () => <svg {...ico} width="18" height="18"><path d="M10 14a4 4 0 0 0 5.7 0l2.3-2.3a4 4 0 0 0-5.7-5.7L11 7" /><path d="M14 10a4 4 0 0 0-5.7 0L6 12.3a4 4 0 0 0 5.7 5.7L13 17" /><path d="M19 2v4M17 4h4" /></svg>;
const REL_ICON = {
  same_as: <svg {...ico}><path d="M5 9h14M5 15h14" /></svg>,
  similar_to: <svg {...ico}><path d="M4 9c2-2 4-2 8 0s6 2 8 0M4 15c2-2 4-2 8 0s6 2 8 0" /></svg>,
  group: <LinkIcon />,
};
const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } };
const TagPlus = () => <svg {...ico} width="18" height="18"><path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z" /><path d="M8 8h.01M16 16v4M14 18h4" /></svg>;

// kind "cat" = neutral chip with an emoji; anything else is a location (icon + color).
const Tag = ({ t, kind, on, caret, onClick, ...rest }) =>
  <button type="button" title={t} className={`tag ${kind === "cat" ? "neutral" : "loc-only"} ${on ? "on" : ""}`} style={{ "--h": tagHue(t) }}
    aria-pressed={caret ? undefined : on} onClick={onClick} {...rest}>
    {kind === "cat" ? <><span aria-hidden="true">{CATEGORY_ICONS[t]}</span> {t}</> : <><LocationIcon t={t} /><span className="sr">{t}</span></>}
    {caret && <span aria-hidden="true"> ▾</span>}
  </button>;

/** `clue` set = editing that clue; otherwise adding a new one.
 *  `prefill` (import) = a new clue with its fields already filled in: { scope, info, general, location, source,
 *  image (a staged image), mediaText (a Street View link for the media slot), mediaError, areaFile (picture of the
 *  map for the region slot), progress }. `onSkip` adds a Skip button next to Save; `skipTo` ([{ key, label, go }])
 *  turns it into a split button (Skip | ▾) whose caret opens one "Skip to <label>" entry each (import: the guide's later sections).
 *  The top part has the same layout as the clue card (image left; tags, text and sentences right);
 *  everything else sits below it. The region of a regional photo is drawn ON the photo (like the card),
 *  by AreaPicker, through a portal into the map box ClueImage renders.
 *  Ratings: on a NEW clue (created or imported) they are filled automatically (see autoRatings.js) and follow the text,
 *  the relations and the region until the user sets that rating by hand (× on a rating gives it back to the automation).
 *  Each rating is independent. Editing an existing clue never touches its ratings. */
export default function ClueEditor({ cc, name, clue, prefill, onClose, onSaved, onSkip, skipTo }) {
  const { nameOf } = useCountries();
  const [scope, setScope] = useState(clue?.scope ?? prefill?.scope ?? "nat");
  const [info, setInfo] = useState(clue?.info ?? prefill?.info ?? "");
  const [location, setLocation] = useState(clue?.tags?.find((t) => LOCATION_TAGS.includes(t)) ?? prefill?.location ?? "");
  const [general, setGeneral] = useState(clue?.tags?.find((t) => GENERAL_TAGS.includes(t)) ?? prefill?.general ?? "");
  const [locTouched, setLocTouched] = useState(!!clue || !!prefill); // user picked "where it applies" by hand
  const [genAuto, setGenAuto] = useState(false); // the category was guessed from the text
  const [menu, setMenu] = useState(null); // null | "cat" | "loc"
  const [skipOpen, setSkipOpen] = useState(false); // the "Skip to <section>" dropdown
  const [tab, setTab] = useState("import"); // regional media slot: import | draw
  const [rating, setRating] = useState(() => Object.fromEntries(RATINGS.map(([k]) => [k, clue?.[k] != null ? String(clue[k]) : ""])));
  // manual[k] = the user set this rating by hand, so the automation leaves it alone. An existing clue is all manual.
  const [manual, setManual] = useState(() => Object.fromEntries(RATINGS.map(([k]) => [k, !!clue])));
  const [deck, setDeck] = useState(clue?.deck ?? "");
  const [decks, setDecks] = useState([]);
  // New clues start in the deck used last (the server also falls back to it when this is empty).
  useEffect(() => {
    let off = false;
    api("/settings").then((s) => {
      if (off) return;
      setDecks(s.decks ?? []);
      setDeck((d) => d || s.last_deck || s.decks?.[0] || "");
    }).catch(() => {});
    return () => { off = true; };
  }, []);
  const [blur, setBlur] = useState(!!clue?.blur);
  const [maxH, setMaxH] = useState(clue?.max_height ?? null); // px cap for the image on the card
  const [img, setImg] = useState({ has: !!clue?.image, sig: ":0" }); // reported by ClueImage
  // The media slot: an image (handled by ClueImage), or an SVG / CSV / geometry staged here.
  const [media, setMedia] = useState(() => initialMedia(clue));
  // The region of a photo: required whenever the media is an image (same SVG / CSV / geometry kinds).
  const [area, setArea] = useState(() => initialArea(clue));
  const [ovEl, setOvEl] = useState(null); // the map box on the photo, where the region is drawn
  const [incoming, setIncoming] = useState(prefill?.image ?? null); // a staged image to hand to ClueImage
  const [imageGone, setImageGone] = useState(false);
  const [mediaErr, setMediaErr] = useState(prefill?.mediaError ?? "");
  const [mediaBusy, setMediaBusy] = useState(false);
  const [stagedKind] = useState(() => ({ media: initialMedia(clue).kind, area: initialArea(clue).kind }));
  const [source, setSource] = useState(clue?.source ?? prefill?.source ?? "");
  const [relations, setRelations] = useState(clue?.relations ?? []);
  const [notes, setNotes] = useState(clue?.relation_notes ?? {});
  const [medal, setMedal] = useState(clue?.medal ? { medal: clue.medal, score: clue.score } : null); // what the card would show
  const [srcOpen, setSrcOpen] = useState(false);
  const [picking, setPicking] = useState(false);
  const [others, setOthers] = useState({}); // cc -> that country's clues, only to describe the relation chips
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [confirm, setConfirm] = useState(false);
  const textEd = useRef(null);
  const slot = useRef(null);
  const skipRef = useRef(null);
  const colRef = useRef(null);
  const imgApi = useRef(null);
  const srcRef = useRef(null);
  const srcInput = useRef(null);
  const asked = useRef(new Set());

  const mainKind = media.kind ?? (img.has ? "image" : null); // what the media slot is showing now
  const isImage = mainKind === "image";
  const showArea = scope === "reg" && isImage; // a photo needs a region; an SVG/CSV/geometry is its own
  const drawing = scope === "reg" && tab === "draw";
  const modeToggle = scope === "reg" && (tab === "draw" || !isImage)
    ? <div className="ed-mode" role="group" aria-label="Region source">
        {[["import", "Import"], ["draw", "Draw"]].map(([k, l]) =>
          <button key={k} type="button" aria-pressed={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}
      </div>
    : null;

  // ── automatic ratings (new clues only) ──
  // The region's numbers come from the geometry the server staged (polygons, shades, separate pieces, spread).
  const regionUp = scope === "reg" ? (showArea ? area.upload : mainKind === "geometry" ? media.upload : null) : null;
  const auto = useMemo(() => autoRatings({
    scope, info, relations, stats: regionUp?.stats ?? null, fromMapmaking: regionUp?.name === MAPMAKING_FILE,
  }), [scope, info, relations, regionUp]);
  useEffect(() => {
    if (clue) return;
    setRating((r) => {
      const next = { ...r };
      let changed = false;
      for (const [k] of RATINGS) if (!manual[k] && next[k] !== auto[k]) { next[k] = auto[k]; changed = true; }
      return changed ? next : r;
    });
  }, [auto, manual, clue]);
  const setRatingByHand = (k, v) => {
    setRating((r) => ({ ...r, [k]: v }));
    // Setting a value takes the rating over; clearing it (×) hands it back to the automation (new clues only).
    setManual((m) => ({ ...m, [k]: !!clue || v !== "" }));
  };

  // Only ratings set by hand count as "changes": the automatic ones alone shouldn't make a fresh form look dirty.
  const snapshot = () => JSON.stringify([scope, info, general, location, RATINGS.map(([k]) => (manual[k] ? rating[k] : null)), blur, maxH,
    img.sig, source, relations, notes, mainKind, media.upload?.token ?? "", area.kind, area.upload?.token ?? ""]);
  const initial = useRef(snapshot());
  const dirty = snapshot() !== initial.current;
  const requestClose = () => (dirty ? setConfirm(true) : onClose());
  const closeRef = useRef(requestClose);
  closeRef.current = requestClose;

  // Escape closes the open tag menu / skip menu first, then asks before throwing work away.
  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Escape") return;
      if (menu) setMenu(null);
      else if (skipOpen) setSkipOpen(false);
      else closeRef.current();
    };
    const onDown = (e) => {
      if (menu && slot.current && !slot.current.contains(e.target)) setMenu(null);
      if (skipOpen && skipRef.current && !skipRef.current.contains(e.target)) setSkipOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("mousedown", onDown); };
  }, [menu, skipOpen]);

  // When the source box opens, focus it; clicking anywhere outside compresses it again.
  useEffect(() => {
    if (!srcOpen) return;
    srcInput.current?.focus();
    const onDown = (e) => { if (srcRef.current && !srcRef.current.contains(e.target)) setSrcOpen(false); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [srcOpen]);

  // Load the clues on the other end of each relation, so the chips can say which clue they point at.
  useEffect(() => {
    for (const code of new Set(relations.map((r) => r.cc))) {
      if (asked.current.has(code)) continue;
      asked.current.add(code);
      api(`/clues/${code}`).then((d) => setOthers((o) => ({ ...o, [code]: d }))).catch(() => {});
    }
  }, [relations]);

  const pickCategory = (t) => {
    setGeneral(t);
    setGenAuto(false);
    if (!locTouched) setLocation(DEFAULT_LOCATION[t] ?? "Anywhere");
    setMenu(null);
  };
  const pickLocation = (t) => { setLocation(t); setLocTouched(true); setMenu(null); };
  const toggle = (m) => setMenu((cur) => (cur === m ? null : m));

  const present = (t) => relations.some((r) => r.type === t);
  const note = (t) => ({ verb: "uses", text: "", ...notes[t] });
  const setNote = (t, patch) => setNotes((n) => ({ ...n, [t]: { verb: "uses", text: "", ...n[t], ...patch } }));
  const addRelation = (r) => { setRelations((rs) => (rs.some((x) => sameRel(x, r)) ? rs : [...rs, r])); setPicking(false); };
  const removeRelation = (r) => setRelations((rs) => rs.filter((x) => !sameRel(x, r)));
  const snippetOf = (r) => others[r.cc]?.find((c) => c.id === r.id);
  // Grouped clues share the tags of the clue they are grouped with. A new clue gets them filled in;
  // once grouped, the tags can't be changed by hand (ungroup to edit them).
  const grouped = relations.some((r) => r.type === "group");
  useEffect(() => {
    if (general || location) return;
    const g = relations.find((r) => r.type === "group");
    const o = g && snippetOf(g);
    if (!o) return;
    const t = o.tags ?? [];
    setLocation(t.find((x) => LOCATION_TAGS.includes(x)) ?? "");
    setGeneral(t.find((x) => GENERAL_TAGS.includes(x)) ?? "");
  }, [relations, others]); // eslint-disable-line react-hooks/exhaustive-deps
  // Until the user picks a category, guess it (and where it applies) from the text.
  useEffect(() => {
    if (clue || grouped || (general && !genAuto)) return;
    const g = guessCategory(info) ?? "";
    if (g === general) return;
    setGeneral(g);
    setGenAuto(!!g);
    if (!locTouched) setLocation(g ? DEFAULT_LOCATION[g] ?? "Anywhere" : "");
  }, [info, grouped]); // eslint-disable-line react-hooks/exhaustive-deps
  const host = hostOf(source.trim());
  const srcBad = !!source.trim() && !URL_OK.test(source.trim());

  // ── media / region staging ──────────────────────────────────────────────
  // Anything pasted into the media slot ends up here, except plain images (ClueImage edits those itself).
  const stageInput = async (input) => {
    const raw = input.file ? "" : String(input.path ?? input.text ?? "").trim().replace(/^"|"$/g, "");
    // Short links (maps.app.goo.gl) are followed by the backend; this throws if one can't be opened.
    const link = input.file ? null : await resolveStreetView(raw);
    if (link) {
      if (!streetViewAvailable()) throw new Error("Street View isn't available with the current map source. Pick one in Settings.");
      const up = await stageFile(await streetViewShot(link));
      return { ...up, kind: "image" };
    }
    const kind = input.file ? kindOfFile(input.file) : kindOfName(raw);
    if (!kind) throw new Error("That file isn't an image, SVG, CSV or geometry file.");
    const endpoint = kind === "geometry" ? "geometry" : kind;
    const areasOf = kind === "csv" ? cc : undefined; // a CSV becomes named, colored areas for this country
    const up = input.file ? await stageFile(input.file, endpoint, areasOf) : await stagePath(raw, endpoint, areasOf);
    return { ...up, kind: up.kind ?? kind };
  };

  const takeMedia = async (input, { quiet = false } = {}) => {
    setMediaErr(""); setMediaBusy(true);
    try {
      const up = await stageInput(input);
      if (up.kind === "image") {
        setIncoming(up);
        setImageGone(false);
        setMedia({ kind: "image", upload: null });
      } else {
        discardStaged(media.upload?.token);
        // A file that already shows the region needs no second component.
        if (area.upload) { discardStaged(area.upload.token); setArea({ kind: null, upload: null }); }
        setMedia({ kind: uiKind(up.kind), upload: up });
      }
    } catch (e) { if (!quiet) setMediaErr(e.message); return false; } finally { if (!quiet) setMediaBusy(false); }
    return true;
  };

  const takeArea = (up) => {
    discardStaged(area.upload?.token);
    setArea(up ? { kind: uiKind(up.kind), upload: up } : { kind: null, upload: null });
  };

  const takeDrawn = (up) => {
    discardStaged(media.upload?.token);
    if (area.upload) { discardStaged(area.upload.token); setArea({ kind: null, upload: null }); }
    setMedia(up ? { kind: "geometry", upload: up } : { kind: null, upload: null });
  };

  // Imported regional clue: the Street View picture goes into the media slot as soon as the editor opens;
  // if it can't be loaded, the clue's Plonkit picture takes its place.
  useEffect(() => {
    if (!prefill?.mediaText) return;
    (async () => {
      try {
        const ok = await takeMedia({ text: prefill.mediaText }, { quiet: !!prefill?.fallbackImage });
        if (ok || !prefill?.fallbackImage) return;
        const file = await prefill.fallbackImage();
        if (file) await takeMedia({ file });
      } catch (e) {
        setMediaErr(e.message);
      } finally {
        setMediaBusy(false);
      }
    })();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // The Plonkit picture of a clue without a usable Street View link arrives after the editor opened.
  useEffect(() => { if (prefill?.imageFile) takeMedia({ file: prefill.imageFile }); }, [prefill?.imageFile]); // eslint-disable-line react-hooks/exhaustive-deps

  // Removing the photo leaves its region: that becomes the clue's media instead of losing it.
  const removeMedia = () => {
    setIncoming(null);
    setImageGone(true);
    setMedia(area.kind ? { kind: area.kind, upload: area.upload } : { kind: null, upload: null });
    setArea({ kind: null, upload: null });
  };

  const storedMedia = stagedKind.media;
  const storedArea = stagedKind.area;
  const mediaChanged = mainKind !== storedMedia;
  const areaChanged = (showArea ? area.kind : null) !== storedArea;

  const problems = [];
  if (!info.trim()) problems.push("Write what the clue teaches.");
  if (!general || !location) problems.push("Add a tag.");
  if (scope === "nat" && !isImage) problems.push("Add an image.");
  if (scope === "reg" && !mainKind) problems.push("Add an image, SVG map, CSV, geometry file or Street View link.");
  if (showArea && !area.kind) problems.push("Add the region for the photo: an SVG map, a CSV, a geometry file or a picture of the map.");
  if (source.trim() && !URL_OK.test(source.trim())) problems.push("The source must be a link starting with http:// or https://.");
  for (const t of NOTE_TYPES) if (present(t) && !note(t).text.trim()) problems.push(`Finish the "${REL_LABEL[t]}" sentence.`);

  const payload = (imageToken) => {
    const num = (v) => (v === "" ? null : Number(v));
    const upload = {};
    if (imageToken) upload.image = imageToken;
    const put = (u) => { if (u?.token) upload[u.kind] = u.token; };
    put(showArea ? area.upload : media.upload);
    // Files of a kind the clue no longer uses are deleted with it.
    const used = new Set(Object.keys(upload));
    if (isImage) used.add("image");
    if (!isImage && mainKind === "svg") used.add("svg");
    if (showArea && area.kind === "svg") used.add("svg");
    if (!isImage && mainKind === "csv") used.add("csv");
    if (showArea && area.kind === "csv") used.add("csv");
    if (!isImage && mainKind === "geometry") used.add(media.upload?.kind ?? geoKindOf(clue) ?? "");
    if (showArea && area.kind === "geometry") used.add(area.upload?.kind ?? geoKindOf(clue) ?? "");
    const remove = clue && (mediaChanged || areaChanged)
      ? ["image", ...GEOM_FIELDS, "svg", "csv"].filter((k) => clue[k] && !used.has(k)) : [];
    return {
        scope, info, tags: [location, general], deck, blur: isImage && blur,
      max_height: isImage ? maxH : null,
      frequency: num(rating.frequency), ease: num(rating.ease), reliability: num(rating.reliability),
      source: source.trim(),
      relations,
      relation_notes: Object.fromEntries(NOTE_TYPES.filter(present).map((t) => [t, note(t)])),
      files: upload,
      remove,
    };
  };

  // The score and medal the clue would get if it were saved now; the editor follows every change.
  const medalKey = JSON.stringify([scope, info, general, location, rating, relations, mainKind, media.upload?.token ?? "", area.kind, area.upload?.token ?? ""]);
  useEffect(() => {
    if (!general || !location) { setMedal(null); return undefined; }
    let off = false;
    const t = setTimeout(() => {
      api(`/clues/${cc}/medal`, { method: "POST", body: { ...payload(null), clue_id: clue?.id ?? null } })
        .then((r) => !off && setMedal(r.medal ? r : null)).catch(() => {});
    }, 400);
    return () => { off = true; clearTimeout(t); };
  }, [medalKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    setSaving(true); setErr("");
    try {
      // Edits to the image stay in memory until now; this uploads the final pixels (or reuses the staged file).
      const imageToken = isImage ? await imgApi.current?.resolve() : null;
      await api(clue ? `/clues/${cc}/${clue.id}` : `/clues/${cc}`, { method: clue ? "PUT" : "POST", body: payload(imageToken) });
      onSaved();
    } catch (e) { setErr(errorText(e)); setSaving(false); }
  };

  // Same badges as the card. The warning / info ones follow the relations by themselves.
  const badges = <div className="clue-badges ed-badges">
    {medal && <img className="cb-img medal" src={`/medals/${medal.medal}.png`} alt={`${medal.medal} medal`}
      title={`${medal.medal[0].toUpperCase()}${medal.medal.slice(1)} medal · ${medal.score}/10`} />}
    {present("same_as") && <span className="cb" role="img" aria-label="Same clue in other countries" title="Same clue in other countries (from relations)">⚠️</span>}
    {present("similar_to") && <span className="cb" role="img" aria-label="Similar clue in other countries" title="Similar clue in other countries (from relations)">ℹ️</span>}
  </div>;

  // What the media box shows when it is not a picture. A stored CSV / geometry is drawn on its country.
  const mediaPreview = media.kind && media.kind !== "image"
    ? media.upload
      ? <Preview kind={media.kind} v={media.upload} cc={cc} />
      : media.kind === "svg"
        ? <img className="ed-media-img" src={fileUrl(cc, clue?.svg, clue?.rev)} alt="" />
        : clue ? <div className="clue-full"><ClueMap cc={cc} clue={clue} /></div> : undefined
    : undefined;

  // The region a stored photo already has. It is drawn on the photo (AreaPicker), so the slot only names it.
  const areaCurrent = clue && showArea
    ? area.kind === "svg" && clue.svg ? { text: "Current SVG" }
      : area.kind === "csv" && clue.csv ? { text: `Current CSV${clue.csv_rows ? ` (${clue.csv_rows.length - 1} rows)` : ""}` }
        : area.kind === "geometry" && geoKindOf(clue) ? { text: `Current geometry (${geoKindOf(clue)})` } : null
    : null;

  // National clues only take an image: switching back drops an SVG / CSV / geometry picked for a region.
  const changeScope = (k) => {
    setScope(k);
    if (k === "nat" && media.kind && media.kind !== "image") {
      discardStaged(media.upload?.token);
      setMedia({ kind: null, upload: null });
    }
    if (k === "nat" && area.upload) takeArea(null);
  };

  // Toolbar buttons must not steal the text selection from the editor.
  const keepSel = (e) => e.preventDefault();

  return <div className="modal-back" onMouseDown={(e) => { if (e.target === e.currentTarget) requestClose(); }}>
    <div className="modal wide" role="dialog" aria-modal="true" aria-label={`${clue ? "Edit" : "Add"} clue for ${name}`}>
      <div className="modal-head">
        <h3>{clue ? "Edit clue" : "Add clue"} · {name}{prefill?.progress ? ` · ${prefill.progress}` : ""}</h3>
        <div className="tabs" role="group" aria-label="Scope">
          {[["nat", "National"], ["reg", "Regional"]].map(([k, l]) =>
            <button key={k} type="button" aria-pressed={scope === k} className={scope === k ? "on" : ""} disabled={!!clue && scope !== k} onClick={() => changeScope(k)}>{l}</button>)}
        </div>
      </div>

      {/* Same grid as the clue card: media on the left, body on the right. */}
      <div className="clue ed-clue">
        <div className="ed-left" ref={colRef}>
          {drawing ? <DrawMap cc={cc} stage={stageInput} onResult={takeDrawn} trailing={modeToggle} /> : <ClueImage ref={imgApi} current={imageGone ? null : clue?.image ? fileUrl(cc, clue.image, clue.rev) : null} colRef={colRef} overlay={badges}
            preview={mediaPreview} incoming={incoming}
            regionSlot={showArea} onOverlay={setOvEl} loading={mediaBusy || (!!prefill?.pending && !mainKind)}
            onOtherFile={(f) => takeMedia({ file: f })} onOtherText={(t) => takeMedia({ text: t })} onRemove={removeMedia}
            blur={blur} onBlur={setBlur} maxH={maxH} onMaxH={setMaxH} trailing={modeToggle}
            onState={(h, sig) => setImg((p) => (p.has === h && p.sig === sig ? p : { has: h, sig }))} />}
          {mediaErr && <p className="err">{mediaErr}</p>}
          {/* A photo can't say which region it is: the region is mandatory in that case. It is drawn on the photo above. */}
          {showArea && <AreaPicker cc={cc} value={area.upload} current={areaCurrent} onChange={takeArea} stage={stageInput}
            initialPicture={prefill?.areaFile} overlayEl={ovEl} clue={clue} loading={!!prefill?.pending} />}
          <div className="ed-rels">
            {relations.map((r) => {
              const o = snippetOf(r);
              const thumb = o?.image ? fileUrl(r.cc, o.image, o.rev) : null;
              return <span key={`${r.type}-${r.cc}-${r.id}`} className={`rel-chip ed-rel ${r.type}`}
                title={`${REL_LABEL[r.type]}${r.type === "group" ? "" : ` · ${nameOf(r.cc)}`}${o ? ` — ${trunc(o.info, 80)}` : ""}`}>
                {REL_ICON[r.type]}
                {r.type !== "group" && <Flag code={r.cc} width={16} />}
                {thumb ? <img className="ed-rel-img" src={thumb} alt="" /> : o && <span>{trunc(o.info, 26)}</span>}
                <button type="button" aria-label="Remove relation" onClick={() => removeRelation(r)}>×</button>
              </span>;
            })}
            <button type="button" className="ed-tool ed-add" aria-label="Relate to another clue" title="Relate to another clue" onClick={() => setPicking(true)}><LinkPlus /></button>
          </div>
        </div>
        <div className="clue-body ed-body">
          <div className="ed-tagrow">
            <div className="clue-tags tag-slot" ref={slot}>
              {general
                ? grouped
                  ? <>
                      <Tag t={location} kind="loc" on disabled aria-pressed={undefined} title="Tags come from the grouped clue — ungroup to change them" />
                      <Tag t={general} kind="cat" on disabled aria-pressed={undefined} title="Tags come from the grouped clue — ungroup to change them" />
                    </>
                  : <>
                      <Tag t={location} kind="loc" on caret aria-haspopup="true" aria-expanded={menu === "loc"} onClick={() => toggle("loc")} />
                      <Tag t={general} kind="cat" on caret aria-haspopup="true" aria-expanded={menu === "cat"} onClick={() => toggle("cat")} />
                    </>
                : <button type="button" className="tag-empty" aria-label="Add tag" title="Add tag" aria-haspopup="true" aria-expanded={menu === "cat"}
                    onClick={() => toggle("cat")}><TagPlus /></button>}
              {menu === "cat" && <div className="tag-pop" role="group" aria-label="Category">
                {GENERAL_TAGS.map((t) => <Tag key={t} t={t} kind="cat" on={general === t} onClick={() => pickCategory(t)} />)}
              </div>}
              {menu === "loc" && <div className="tag-pop small" role="group" aria-label="Where it applies">
                {LOCATION_TAGS.map((t) => <Tag key={t} t={t} kind="loc" on={location === t} onClick={() => pickLocation(t)} />)}
              </div>}
            </div>
          </div>

          {/* Bold / italic / underline show formatted as you type; only links stay as [text](url). */}
          <RichEditor ref={textEd} value={info} onChange={setInfo} label="Info" placeholder="What does this clue teach?" />
          <div className="rt-row">
            <div className="rt-bar" role="toolbar" aria-label="Text formatting">
              <button type="button" title="Bold (Ctrl+B)" aria-label="Bold" onMouseDown={keepSel} onClick={() => textEd.current.format("bold")}><b>B</b></button>
              <button type="button" title="Italic (Ctrl+I)" aria-label="Italic" onMouseDown={keepSel} onClick={() => textEd.current.format("italic")}><i>I</i></button>
              <button type="button" title="Underline (Ctrl+U)" aria-label="Underline" onMouseDown={keepSel} onClick={() => textEd.current.format("underline")}><u>U</u></button>
              <button type="button" title="Link" aria-label="Link" onMouseDown={keepSel} onClick={() => textEd.current.link()}><LinkIcon /></button>
            </div>
            {/* The card's source chip (site icon). Click it to edit the link: it widens into a text box. */}
            <div className={`chip neutral src ed-src ${srcOpen ? "open" : ""} ${srcBad ? "bad" : ""}`} ref={srcRef}>
              <button type="button" className="ed-src-btn" aria-label="Edit source link" title={host || "Add a source link"} aria-expanded={srcOpen}
                onClick={() => setSrcOpen((o) => !o)}>
                {host ? <img src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=32`} alt="" width="16" height="16"
                  onError={(e) => { e.currentTarget.style.display = "none"; }} /> : <LinkIcon />}
              </button>
              <input type="text" ref={srcInput} value={source} aria-label="Source link" placeholder="https://…" aria-invalid={srcBad}
                tabIndex={srcOpen ? 0 : -1} aria-hidden={!srcOpen}
                onChange={(e) => setSource(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setSrcOpen(false); } }} />
            </div>
          </div>

          {/* The sentences the card shows under the text. */}
          {NOTE_TYPES.filter(present).map((t) => {
            const codes = uniq(relations.filter((r) => r.type === t).map((r) => r.cc));
            const n = note(t);
            return <div className={`rel-note ${t}`} key={t}>
              <span>{joinNames(codes.map(nameOf))}</span>
              <select aria-label={`${REL_LABEL[t]} verb`} value={n.verb} onChange={(e) => setNote(t, { verb: e.target.value })}>
                {VERBS.map((v) => <option key={v} value={v}>{verbFor(v, codes.length)}</option>)}
              </select>
              {t === "similar_to" && <span>similar</span>}
              <input type="text" aria-label={`${REL_LABEL[t]} sentence`} value={n.text} placeholder="…finish the sentence"
                onChange={(e) => setNote(t, { text: e.target.value })} />
            </div>;
          })}
        </div>
      </div>

      <div className="ed-extra">
        <div className="rates-dots">
          {RATINGS.map(([k, label, lo, hi]) => <RatingDots key={k} label={label} lo={lo} hi={hi} value={rating[k]}
            onChange={(v) => setRatingByHand(k, v)} />)}
          {!clue && <p className="muted">Ratings fill in by themselves until you set one. × gives it back to the automation.</p>}
        </div>
      </div>

      {err && <p className="err">{err}</p>}
      <div className="ed-foot">
        {problems.length > 0 && <p className="muted problems">{problems[0]}</p>}
        <button type="button" className="btn ghost" onClick={requestClose}>Cancel</button>
        {/* Split button: "Skip" with the section dropdown glued to its right edge. */}
        {onSkip && (skipTo?.length > 0
          ? <div className="ed-skip" ref={skipRef}>
              <button type="button" className="btn ghost ed-skip-main" disabled={saving} onClick={onSkip}>Skip</button>
              <button type="button" className="btn ghost ed-skip-caret" aria-haspopup="menu" aria-expanded={skipOpen}
                aria-label="Skip to a section" title="Skip to a section" disabled={saving} onClick={() => setSkipOpen((o) => !o)}>
                <span aria-hidden="true">{skipOpen ? "▴" : "▾"}</span></button>
              {skipOpen && <div className="ed-skip-menu" role="menu" aria-label="Skip to a section">
                {skipTo.map((s) => <button key={s.key} type="button" role="menuitem" className="ed-skip-item"
                  style={s.head ? { fontWeight: 700 } : { paddingLeft: 24 }}
                  onClick={() => { setSkipOpen(false); s.go(); }}>
                  {s.head ? `Skip to ${s.label}` : <><span aria-hidden="true">{CATEGORY_ICONS[s.category]}</span> {s.label}</>}
                </button>)}
              </div>}
            </div>
          : <button type="button" className="btn ghost" disabled={saving} onClick={onSkip}>Skip</button>)}
        <button type="button" className="btn" title={problems[0]} disabled={saving || problems.length > 0} onClick={save}>{saving ? "Saving…" : "Save clue"}</button>
      </div>
    </div>
    {picking && <RelatePicker cc={cc} clueId={clue?.id ?? null} tags={{ general, location }} taken={relations}
      onPick={addRelation} onClose={() => setPicking(false)} />}
    {confirm && <ConfirmDialog title={clue ? "Discard your changes?" : "Discard this clue?"} body="What you entered won't be saved."
      confirmLabel="Discard" cancelLabel="Keep editing" danger onConfirm={onClose} onCancel={() => setConfirm(false)} />}
  </div>;
}
