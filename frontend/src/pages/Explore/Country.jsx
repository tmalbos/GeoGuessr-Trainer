import { useEffect, useState } from "react";
import Flag from "../../components/Flag.jsx";
import CountryMap from "./CountryMap.jsx";
import { loadWorld } from "./geo.js";
import SECTIONS from "./sections.js";

export default function Country({ id, onBack }) {
  const [name, setName] = useState("");
  const [active, setActive] = useState(null);

  useEffect(() => {
    let off = false;
    setName("");
    loadWorld().then((w) => !off && setName(w.byId[id]?.name ?? id)).catch(() => !off && setName(id));
    return () => { off = true; };
  }, [id]);

  const sections = SECTIONS.filter((s) => !s.available || s.available(id));
  const current = sections.find((s) => s.id === active) ?? sections[0];
  const title = name || id;

  return <div className="cp">
    <section className="cp-main" aria-label={`${title} clues`}>
      {current
        ? <current.Component id={id} name={title} />
        : <div className="cp-empty">
            <h3>Coming soon</h3>
            <p className="muted">There are no clues for {title} yet. Sections will appear here as they are added.</p>
          </div>}
    </section>
    <aside className="cp-side" aria-label={`${title} navigation`}>
      <button type="button" className="btn ghost cp-back" onClick={onBack}>← World map</button>
      <div className="cp-id">
        <Flag code={id} width={40} />
        <h2>{title}</h2>
      </div>
      {sections.length > 0 && <nav className="tabs cp-tabs" aria-label="Sections">
        {sections.map((s) => <button key={s.id} type="button" className={s === current ? "on" : ""}
          aria-current={s === current ? "page" : undefined} onClick={() => setActive(s.id)}>{s.label}</button>)}
      </nav>}
      <CountryMap id={id} />
    </aside>
  </div>;
}
