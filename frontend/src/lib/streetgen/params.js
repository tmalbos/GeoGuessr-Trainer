// params.js: every number a rule depends on lives here, plus parameter normalisation.
// Coordinates everywhere are metres, origin at the map centre, +x east, +y north.

export const VERSION = 1;
export const STYLES = ["planned", "organic"];
export const WATER_KINDS = ["none", "river", "lake", "coast", "random"];
export const CLS_RANK = { tertiary: 0, secondary: 1, primary: 2 };

// Widths in metres [min, max]. A road gets the middle value unless the generator picks another.
export const WIDTHS = {
  highway: [24, 28],
  primary: [12, 21],
  secondary: [8, 14],
  tertiary: [4, 7],
  rail: [3, 4],
};
export const midWidth = (kind) => (WIDTHS[kind][0] + WIDTHS[kind][1]) / 2;
export const widthKind = (e) => (e.highway ? "highway" : e.cls);

export const LIMITS = {
  minEdge: 30,            // shortest edge (m)
  minAngle: 20,           // sharpest junction (degrees)
  snapTol: 25,            // a road end this close to a valid road joins it
  extend: 60,             // a road end may be stretched this far to reach a valid road
  borderTol: 1,           // an end this close to the map edge sits on it
  mergeTol: 0.5,          // points closer than this are the same point
  touchTol: 0.05,         // closer than this without a shared node = a "touch" violation
  nearMiss: 6,            // a free end closer than this to another road is a glitch
  bridgeClearance: 8,     // extra metres of bridge deck on each side of what it crosses
  bridgeSpacing: [300, 1000],
  rampSpacing: [1000, 3000],
  rampSlack: 0.25,
  primaryVsCity: 0.3,     // R13: median primary length / city diameter
  secondaryBlocks: 8,     // R13: median secondary length / block size
};

export const DEFAULT_PARAMS = {
  style: "planned", sizeKm: 6, seed: 1, irregularity: 0.4, water: "none", highway: false, rail: false, blockSize: null,
};

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const num = (v, d) => (Number.isFinite(Number(v)) ? Number(v) : d);

export function normalizeParams(p = {}) {
  const q = { ...DEFAULT_PARAMS, ...p };
  const style = STYLES.includes(q.style) ? q.style : DEFAULT_PARAMS.style;
  return {
    style,
    sizeKm: clamp(num(q.sizeKm, 6), 3, 16),
    seed: Math.trunc(num(q.seed, 1)),
    irregularity: clamp(num(q.irregularity, 0.4), 0, 1),
    water: WATER_KINDS.includes(q.water) ? q.water : "none",
    highway: !!q.highway,
    rail: !!q.rail,
    blockSize: q.blockSize == null ? null : clamp(num(q.blockSize, 100), 40, 250),
  };
}

export const boundsFor = (sizeKm) => {
  const h = sizeKm * 500;
  return { minx: -h, miny: -h, maxx: h, maxy: h };
};
