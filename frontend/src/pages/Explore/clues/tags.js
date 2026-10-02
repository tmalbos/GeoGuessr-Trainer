// Keep in sync with LOCATION_TAGS / GENERAL_TAGS in src/api/routers/clues.py.
export const LOCATION_TAGS = ["Urban", "Rural", "Anywhere"];
export const GENERAL_TAGS = [
  "Architecture", "Phone Numbers", "Postal Codes", "Topography", "Vegetation",
  "Infrastructure", "Vehicle", "License Plate", "Flags", "Roads", "Subdivision Names",
  "City Names", "Agriculture", "Soil", "Brand", "Language", "Script", "Road Sign",
  "Electricity Pole", "Bollard", "National Parks", "Biome", "Religion",
];
export const VISIBILITIES = [["visible", "Visible"], ["guide-only", "Guide only"], ["analysis-only", "Analysis only"]];

const LOCATION_HUES = { Urban: 35, Rural: 140, Anywhere: 220 };
// Golden-angle spacing: every category gets its own, well separated hue.
export const tagHue = (t) => LOCATION_HUES[t] ?? (GENERAL_TAGS.indexOf(t) * 137.5 + 10) % 360;
