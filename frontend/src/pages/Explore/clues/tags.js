import { createElement as h } from "react";

// Keep in sync with LOCATION_TAGS / GENERAL_TAGS in src/api/routers/clues.py.
export const LOCATION_TAGS = ["Urban", "Rural", "Anywhere"];
export const GENERAL_TAGS = [
  "Architecture", "Phone Numbers", "Postal Codes", "Topography", "Vegetation",
  "Infrastructure", "Vehicle", "License Plate", "Flags", "Roads", "Subdivision Names",
  "City Names", "Agriculture", "Soil", "Brand", "Language", "Script", "Road Sign",
  "Electricity Pole", "Bollard", "National Parks", "Biome", "Religion",
  "Animals", "Car Meta", "Copyright", "Generation Meta", "Camera Meta",
];

// [field, label, what 1 means, what 10 means]
export const RATINGS = [
  ["frequency", "Frequency", "rare", "common"],
  ["ease", "Ease", "hard", "easy"],
  ["reliability", "Reliability", "shaky", "solid"],
];

// Picking a category pre-fills "where it applies" with this (until the user picks one themselves).
export const DEFAULT_LOCATION = {
  "Architecture": "Urban", "Phone Numbers": "Urban", "Postal Codes": "Urban", "Topography": "Anywhere",
  "Vegetation": "Rural", "Infrastructure": "Anywhere", "Vehicle": "Anywhere", "License Plate": "Anywhere",
  "Flags": "Urban", "Roads": "Anywhere", "Subdivision Names": "Anywhere", "City Names": "Anywhere",
  "Agriculture": "Rural", "Soil": "Rural", "Brand": "Urban", "Language": "Anywhere", "Script": "Anywhere",
  "Road Sign": "Anywhere", "Electricity Pole": "Anywhere", "Bollard": "Rural", "National Parks": "Rural",
  "Biome": "Anywhere", "Religion": "Urban",
  "Animals": "Rural", "Car Meta": "Anywhere", "Copyright": "Anywhere", "Generation Meta": "Anywhere",
  "Camera Meta": "Anywhere",
};

// ── Custom, slightly cartoonish icons (plain SVG elements, no JSX so this stays a .js file) ──
const el = (tag, props) => h(tag, props);
const svg = (...kids) => h("svg", {
  className: "cat-ico", width: "1.15em", height: "1.15em", viewBox: "0 0 24 24",
  "aria-hidden": true, style: { verticalAlign: "-0.2em" },
}, ...kids);

// Israeli plate: yellow, blue strip on the left
const PLATE = svg(
  el("rect", { x: 1, y: 6, width: 22, height: 12, rx: 2.5, fill: "#f6c619", stroke: "#1b1b1b", strokeWidth: 1 }),
  el("path", { d: "M1 8.5A2.5 2.5 0 0 1 3.5 6H6.5v12H3.5A2.5 2.5 0 0 1 1 15.5z", fill: "#2a5db0" }),
  el("rect", { x: 2.4, y: 9.2, width: 2.4, height: 5.6, rx: 0.6, fill: "#fff" }),
  el("text", { x: 15, y: 14.4, fontSize: 5.2, fontWeight: 800, textAnchor: "middle", fill: "#1b1b1b", fontFamily: "sans-serif" }, "12-345"),
);

// Romanian concrete pole with holes
const POLE = svg(
  el("path", { d: "M3 9Q6 11.5 9.7 8M21 9Q18 11.5 14.3 8", stroke: "#555", strokeWidth: 1, fill: "none", strokeLinecap: "round" }),
  el("path", { d: "M9.5 2h5l1.3 20H8.2z", fill: "#c2bdb0", stroke: "#6f6a5e", strokeWidth: 1, strokeLinejoin: "round" }),
  ...[5.5, 9.5, 13.5, 17.5].map((y) => el("rect", { key: y, x: 11, y, width: 2, height: 2.6, rx: 0.6, fill: "#3b3a36" })),
  el("path", { d: "M6 4.5h12", stroke: "#555", strokeWidth: 1.2, strokeLinecap: "round" }),
);

// Israeli stop: red octagon, white hand
const STOP = svg(
  el("polygon", { points: "8,2 16,2 22,8 22,16 16,22 8,22 2,16 2,8", fill: "#fff", stroke: "#d6281f", strokeWidth: 1 }),
  el("polygon", { points: "8.6,3.8 15.4,3.8 20.2,8.6 20.2,15.4 15.4,20.2 8.6,20.2 3.8,15.4 3.8,8.6", fill: "#d6281f" }),
  el("rect", { x: 9, y: 11, width: 7, height: 7.5, rx: 2.6, fill: "#fff" }),
  el("rect", { x: 9.1, y: 6.2, width: 1.7, height: 6.5, rx: 0.85, fill: "#fff" }),
  el("rect", { x: 11.1, y: 5, width: 1.7, height: 7.5, rx: 0.85, fill: "#fff" }),
  el("rect", { x: 13.1, y: 5.8, width: 1.7, height: 6.7, rx: 0.85, fill: "#fff" }),
  el("rect", { x: 15, y: 7.6, width: 1.5, height: 5, rx: 0.75, fill: "#fff" }),
  el("path", { d: "M9.4 14.6L7.6 12.6", stroke: "#fff", strokeWidth: 1.7, strokeLinecap: "round" }),
);

// Slovakian bollard: white post, orange reflector with a dark stripe
const BOLLARD = svg(
  el("rect", { x: 8.5, y: 2, width: 7, height: 20, rx: 1.6, fill: "#f3f3ef", stroke: "#8a8a84", strokeWidth: 1 }),
  el("rect", { x: 9.8, y: 5.5, width: 4.4, height: 5.2, rx: 0.4, fill: "#f08a1c" }),
  el("rect", { x: 9.8, y: 7.4, width: 4.4, height: 1.3, fill: "#222" }),
);

// Animals: stork standing in its nest
const ANIMALS = svg(
  el("path", { d: "M11 12.5V16M13.5 12.5V16", stroke: "#e23d2f", strokeWidth: 1.2, strokeLinecap: "round" }),
  el("path", { d: "M14.5 8.5Q17.5 7 16.3 5", stroke: "#333", strokeWidth: 3.6, strokeLinecap: "round", fill: "none" }),
  el("path", { d: "M14.5 8.5Q17.5 7 16.3 5", stroke: "#fff", strokeWidth: 2.2, strokeLinecap: "round", fill: "none" }),
  el("ellipse", { cx: 11.5, cy: 10, rx: 5.2, ry: 3.8, fill: "#fff", stroke: "#333", strokeWidth: 1 }),
  el("path", { d: "M7.4 9.2Q11 8.2 14.6 11.4Q10 13 7.4 9.2Z", fill: "#222" }),
  el("circle", { cx: 16.4, cy: 4.2, r: 1.7, fill: "#fff", stroke: "#333", strokeWidth: 1 }),
  el("path", { d: "M17.8 4.2L22.5 5.2", stroke: "#e23d2f", strokeWidth: 1.5, strokeLinecap: "round" }),
  el("path", { d: "M2.5 16.5Q12 25 21.5 16.5Q21.5 14.2 19 14H5Q2.5 14.2 2.5 16.5Z", fill: "#8a5a2b", stroke: "#5b3a18", strokeWidth: 1, strokeLinejoin: "round" }),
  el("path", { d: "M5 17l5 2M9 15.5l8 3M19 16.5l-4 3", stroke: "#c79a62", strokeWidth: 0.9, strokeLinecap: "round" }),
);

// Car Meta: the Ghana car, vertical, filled (not blurred): blue top with two eyes, silver slatted body
const CAR_META = svg(
  el("rect", { x: 6, y: 9, width: 12, height: 13, rx: 1.5, fill: "#c9ccd1", stroke: "#555", strokeWidth: 1 }),
  ...[11.6, 14, 16.4, 18.8].map((y) => el("path", { key: y, d: `M7.5 ${y}H16.5`, stroke: "#858a92", strokeWidth: 0.9, strokeLinecap: "round" })),
  el("rect", { x: 4.5, y: 2, width: 15, height: 8.5, rx: 3.8, fill: "#2b6fd6", stroke: "#174a9a", strokeWidth: 1 }),
  el("circle", { cx: 9.3, cy: 6.2, r: 2.4, fill: "#fff", stroke: "#222", strokeWidth: 0.8 }),
  el("circle", { cx: 14.7, cy: 6.2, r: 2.4, fill: "#fff", stroke: "#222", strokeWidth: 0.8 }),
  el("circle", { cx: 9.6, cy: 6.4, r: 1, fill: "#222" }),
  el("circle", { cx: 14.4, cy: 6.4, r: 1, fill: "#222" }),
);

// Copyright: the (c) sign
const COPYRIGHT = svg(
  el("circle", { cx: 12, cy: 12, r: 9.3, fill: "none", stroke: "#e6edf3", strokeWidth: 2 }),
  el("path", { d: "M15.3 9.4A4.3 4.3 0 1 0 15.3 14.6", fill: "none", stroke: "#e6edf3", strokeWidth: 2.2, strokeLinecap: "round" }),
);

// Generation Meta: small cam, straight: top-down dirt road, edge posts, the small car bump on the right edge
const GEN_META = svg(
  el("rect", { x: 1, y: 3, width: 22, height: 18, rx: 2, fill: "#b79a78", stroke: "#7a6246", strokeWidth: 1 }),
  el("rect", { x: 4, y: 3.6, width: 1.7, height: 3.6, rx: 0.5, fill: "#fff" }),
  el("rect", { x: 9, y: 3.6, width: 1.7, height: 3.6, rx: 0.5, fill: "#fff" }),
  el("rect", { x: 4, y: 16.8, width: 1.7, height: 3.6, rx: 0.5, fill: "#fff" }),
  el("rect", { x: 9, y: 16.8, width: 1.7, height: 3.6, rx: 0.5, fill: "#fff" }),
  el("path", { d: "M23 5Q16.5 12 23 19Z", fill: "#f4f2ea", stroke: "#8a8a84", strokeWidth: 1, strokeLinejoin: "round" }),
  el("rect", { x: 20.4, y: 10, width: 1.7, height: 4, rx: 0.5, fill: "#c0304a" }),
);

// Camera Meta: a leaf
const CAM_META = svg(
  el("path", { d: "M4 20C3.5 10 10 4 20.5 3.5C20.5 14 14 20.5 4 20Z", fill: "#4caf50", stroke: "#2e7d32", strokeWidth: 1, strokeLinejoin: "round" }),
  el("path", { d: "M4.5 19.5L16 8M10 14l-0.5-4.5M13 11.2l3.2 0.3", stroke: "#2e7d32", strokeWidth: 1, strokeLinecap: "round", fill: "none" }),
);

// Category = WHAT it is (neutral chip + icon). Values are emoji strings or ready-made SVG elements.
export const CATEGORY_ICONS = {
  "Architecture": "🏛️", "Phone Numbers": "📞", "Postal Codes": "✉️", "Topography": "⛰️", "Vegetation": "🌿",
  "Infrastructure": "🏗️", "Vehicle": "🚗", "License Plate": PLATE, "Flags": "🚩", "Roads": "🛣️",
  "Subdivision Names": "🗺️", "City Names": "🏙️", "Agriculture": "🌾", "Soil": "🟤", "Brand": "🏷️",
  "Language": "💬", "Script": "🔤", "Road Sign": STOP, "Electricity Pole": POLE, "Bollard": BOLLARD,
  "National Parks": "🏞️", "Biome": "🌲", "Religion": "🛐",
  "Animals": ANIMALS, "Car Meta": CAR_META, "Copyright": COPYRIGHT, "Generation Meta": CAM_META, "Camera Meta": GEN_META,
};

const LOCATION_HUES = { Urban: 35, Rural: 140, Anywhere: 220 };
export const tagHue = (t) => LOCATION_HUES[t] ?? 215;
