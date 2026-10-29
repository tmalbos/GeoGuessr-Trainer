import "./technical.css";

// The shape sliders of the polygonizer. Used by the Technical page and by the clue editor's mapmaking import.
export const SHAPE_DEFAULTS = { link_km: 45, pad_km: 10, curve: 0.5, shades: 0, density_km: 45 };

const Slider = ({ label, value, min, max, step, unit = "", onChange }) => <div className="tp-field">
  <label>{label}<span className="muted">{value}{unit}</span></label>
  <input type="range" min={min} max={max} step={step} value={value} aria-label={label} onChange={(e) => onChange(Number(e.target.value))} />
</div>;

export default function ShapeControls({ params, onChange }) {
  const set = (patch) => onChange({ ...params, ...patch });
  return <>
    <Slider label="Join points closer than" unit=" km" min={5} max={1000} step={5} value={params.link_km} onChange={(v) => set({ link_km: v })} />
    <Slider label="Minimum radius per point" unit=" km" min={0} max={100} step={1} value={params.pad_km} onChange={(v) => set({ pad_km: v })} />
    <Slider label="Curviness" min={0} max={1} step={0.05} value={params.curve} onChange={(v) => set({ curve: v })} />
    <Slider label="Lighter shades for rarer areas" min={0} max={4} step={1} value={params.shades} onChange={(v) => set({ shades: v })} />
    {params.shades > 0 && <Slider label="Density radius" unit=" km" min={5} max={1000} step={5} value={params.density_km} onChange={(v) => set({ density_km: v })} />}
  </>;
}
