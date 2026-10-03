import { useEffect, useState } from "react";
import Flag from "../../components/Flag.jsx";
import { api } from "../../lib/api.js";
import CountryMap from "./CountryMap.jsx";
import CountryStats from "./CountryStats.jsx";
import { loadWorld } from "./geo.js";
import SECTIONS from "./sections.js";

const Pencil = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
  <path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>;

export default function Country({ id, section, onSection, onBack, backLabel = "World map" }) {
  const [name, setName] = useState("");
  const [editing, setEditing] = useState(false);
  const [domain, setDomain] = useState("");

  useEffect(() => {
    let off = false;
    setName("");
    setEditing(false);
    setDomain("");
    api(`/countries/${id}/domain`).then((d) => !off && setDomain(d.domain ?? "")).catch(() => {});
    loadWorld().then((w) => !off && setName(w.byId[id]?.name ?? id)).catch(() => !off && setName(id));
    return () => { off = true; };
  }, [id]);

  const sections = SECTIONS.filter((s) => !s.available || s.available(id));
  const current = sections.find((s) => s.id === section) ?? sections[0];
  const title = name || id;

  return <div className={`cp ${editing ? "editing" : ""}`}>
    <section className="cp-main" aria-label={`${title} clues`}>
      <div className="cp-head">
        <button type="button" className="btn ghost cp-back" onClick={onBack}>← {backLabel}</button>
        <h2>{title}</h2>
        <span />
      </div>
      {current
        ? <current.Component id={id} name={title} editing={editing} />
        : <div className="cp-empty">
            <h3>Coming soon</h3>
            <p className="muted">There are no clues for {title} yet. Sections will appear here as they are added.</p>
          </div>}
    </section>
    <aside className="cp-side" aria-label={`${title} navigation`}>
      <div className="cp-top">
        <div className="cp-id">
          <Flag code={id} width={56} />
          {domain && <span className="cp-tld" title="Internet domain">{domain}</span>}
        </div>
        <button type="button" className={`btn ghost cp-edit ${editing ? "on" : ""}`} aria-pressed={editing}
          aria-label={editing ? "Exit edit mode" : `Edit ${title} clues`} title={editing ? "Exit edit mode" : "Edit clues"}
          onClick={() => setEditing((e) => !e)}><Pencil /></button>
      </div>
      <CountryStats code={id} name={title} />
      {sections.length > 1 && <nav className="tabs cp-tabs" aria-label="Sections">
        {sections.map((s) => <button key={s.id} type="button" className={s === current ? "on" : ""}
          aria-current={s === current ? "page" : undefined} onClick={() => onSection(s.id)}>{s.label}</button>)}
      </nav>}
      <CountryMap id={id} />
    </aside>
  </div>;
}
