import { memo, useId } from "react";
import { shapes } from "./geoDraw.js";

const W = 1000; // keep equal to W in DrawMap.jsx / ShadeDraw.jsx
const MARGIN = 50;

// The path of an item, worked out once (items are only ever added, never edited).
const PATHS = new WeakMap();
const pathOf = (it, fwd) => {
  if (!PATHS.has(it)) PATHS.set(it, it.d ?? shapes(it.geometry, fwd).polys);
  return PATHS.get(it);
};

/** The subdivisions you click with the Fill tool. Memoized: moving the pointer while painting never re-renders them. */
export const Units = memo(function Units({ paths, click, onPick }) {
  return paths.map((u) => <path key={u.id} data-id={u.id} className={`dm-unit ${click ? "click" : ""}`} d={u.d} fillRule="evenodd"
    style={click ? undefined : { pointerEvents: "none" }} onClick={click ? () => onPick(u) : undefined}><title>{u.name}</title></path>);
});

/** What the user has drawn, painted straight from the list of actions (no server round trip), so it shows instantly.
 *  Items: fill / shape = a filled path, stroke = a polyline with width `w` (map units), erase = hides what came before it.
 *  Strokes are clipped to the country outline (`land`). `fillOf(slot)` gives the paint of a slot. Memoized. */
export default memo(function PaintedLayer({ items, fwd, H, land, fillOf }) {
  const uid = useId().replace(/:/g, "");
  const clip = `${uid}-clip`;
  const defs = [];
  let nodes = [];
  items.forEach((it, i) => {
    const d = pathOf(it, fwd);
    if (!d) return;
    const line = it.w != null;
    if (it.op === "erase") {
      const id = `${uid}-e${i}`;
      defs.push(<mask key={id} id={id} maskUnits="userSpaceOnUse" x={-MARGIN} y={-MARGIN} width={W + 2 * MARGIN} height={H + 2 * MARGIN}>
        <rect x={-MARGIN} y={-MARGIN} width={W + 2 * MARGIN} height={H + 2 * MARGIN} fill="#fff" />
        {line
          ? <path d={d} fill="none" stroke="#000" strokeWidth={it.w} strokeLinecap="round" strokeLinejoin="round" />
          : <path d={d} fill="#000" fillRule="evenodd" />}
      </mask>);
      nodes = [<g key={id} mask={`url(#${id})`}>{nodes}</g>];
      return;
    }
    const paint = fillOf(it.slot);
    nodes.push(line
      ? <path key={i} d={d} fill="none" strokeWidth={it.w} strokeLinecap="round" strokeLinejoin="round" style={{ stroke: paint }} clipPath={`url(#${clip})`} />
      : <path key={i} d={d} fillRule="evenodd" style={{ fill: paint }} />);
  });
  return <>
    <defs><clipPath id={clip}><path d={land} fillRule="evenodd" /></clipPath>{defs}</defs>
    <g style={{ pointerEvents: "none" }}>{nodes}</g>
  </>;
});
