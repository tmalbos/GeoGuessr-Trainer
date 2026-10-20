import { navigate } from "../../lib/router.js";
import Polygonizer from "./Polygonizer.jsx";
import StainTool from "./StainTool.jsx";

// To add a tool: build a component, import it and add one entry. Tabs appear once there are 2+.
const TOOLS = [
  { id: "polygonizer", label: "Polygonizer", Component: Polygonizer },
  { id: "stain", label: "Stain to polygons", Component: StainTool },
];

export default function Technical({ seg }) {
  const tool = TOOLS.find((t) => t.id === seg[0]) ?? TOOLS[0];
  return <>
    <h2>Technical</h2>
    {TOOLS.length > 1 && <nav className="tabs" aria-label="Tools">
      {TOOLS.map((t) => <button key={t.id} type="button" className={t === tool ? "on" : ""}
        aria-current={t === tool ? "page" : undefined} onClick={() => navigate(`/technical/${t.id}`)}>{t.label}</button>)}
    </nav>}
    <tool.Component />
  </>;
}
