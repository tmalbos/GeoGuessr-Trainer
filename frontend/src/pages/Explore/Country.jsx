import { useEffect, useState } from "react";
import Flag from "../../components/Flag.jsx";
import { api } from "../../lib/api.js";
import CountryMap from "./CountryMap.jsx";
import CountryStats from "./CountryStats.jsx";
import DrivingSide from "./DrivingSide.jsx";
import { loadWorld } from "./geo.js";
import SECTIONS from "./sections.js";

const svgProps = { width: 18, height: 18, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
const Pencil = () => <svg {...svgProps}><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>;
const Collapse = () => <svg {...svgProps}><rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="3" y="14" width="7" height="7" /><rect x="14" y="14" width="7" height="7" /></svg>;

const NO_INFO = { domain: "", side: null, currencies: [] };
const OUTLINE_BOX = [180, 132]; // max size of the outline in the facts row (px)

export default function Country({ id, section, query, backHref, backLabel = "World map" }) {
  const [name, setName] = useState("");
  const [editing, setEditing] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [info, setInfo] = useState(NO_INFO);

  useEffect(() => {
    let off = false;
    setName("");
    setEditing(false);
    setInfo(NO_INFO);
    api(`/countries/${id}/info`)
      .then((d) => !off && setInfo({ domain: d.domain ?? "", side: d.driving_side, currencies: d.currencies ?? [] }))
      .catch(() => {});
    loadWorld().then((w) => !off && setName(w.byId[id]?.name ?? id)).catch(() => !off && setName(id));
    return () => { off = true; };
  }, [id]);

  const sections = SECTIONS.filter((s) => !s.available || s.available(id));
  const current = sections.find((s) => s.id === section) ?? sections[0];
  const title = name || id;

  return <div className={`cp ${editing ? "editing" : ""}`}>
    <aside className="cp-rail cp-rail-left" aria-label="Navigation">
      <a className="btn ghost cp-back" href={backHref} aria-label={`Back to ${backLabel}`} title={`Back to ${backLabel}`}>← {backLabel}</a>
    </aside>

    <section className="cp-main" aria-label={`${title} clues`}>
      <header className="cp-header">
        <h2>{title}</h2>
        <div className="cp-stats"><CountryStats code={id} name={title} /></div>
      </header>

      <div className="cp-facts" aria-label={`General facts about ${title}`}>
        <div className="cp-outline"><CountryMap id={id} fit={OUTLINE_BOX} /></div>
        <Flag code={id} width={104} />
        {info.domain && <span className="cp-tld" title="Internet domain">{info.domain}</span>}
        <DrivingSide side={info.side} />
        {info.currencies.length > 0 && <span className="cp-cur" title="Currency">{info.currencies.join(" · ")}</span>}
      </div>

      {sections.length > 1 && <nav className="tabs cp-tabs" aria-label="Sections">
        {sections.map((s) => <a key={s.id} href={`#/explore/${id}/${s.id}`} className={s === current ? "on" : ""}
          aria-current={s === current ? "page" : undefined}>{s.label}</a>)}
      </nav>}

      {current
        ? <current.Component id={id} name={title} editing={editing} collapsed={collapsed && !editing} query={query} />
        : <div className="cp-empty">
            <h3>Coming soon</h3>
            <p className="muted">There are no clues for {title} yet. Sections will appear here as they are added.</p>
          </div>}
    </section>

    <aside className="cp-rail cp-rail-right" aria-label="Tools">
      <button type="button" className={`cp-tool ${editing ? "on" : ""}`} aria-pressed={editing}
        aria-label={editing ? "Exit edit mode" : `Edit ${title} clues`} title={editing ? "Exit edit mode" : "Edit clues"}
        onClick={() => setEditing((e) => !e)}><Pencil /></button>
      <button type="button" className={`cp-tool ${collapsed ? "on" : ""}`} aria-pressed={collapsed}
        aria-label={collapsed ? "Expand clues" : "Collapse clues"} title={collapsed ? "Expand clues" : "Collapse clues"}
        onClick={() => setCollapsed((c) => !c)}><Collapse /></button>
      {/* The clue list renders its filter / add / import tools here through a portal (see ClueList). */}
      <div id="clue-filter-slot" />
    </aside>
  </div>;
}
