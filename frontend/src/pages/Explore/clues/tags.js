// Keep in sync with LOCATION_TAGS / GENERAL_TAGS in src/api/routers/clues.py.
export const LOCATION_TAGS = ["Urban", "Rural", "Anywhere"];
export const GENERAL_TAGS = [
  "Architecture", "Phone Numbers", "Postal Codes", "Topography", "Vegetation",
  "Infrastructure", "Vehicle", "License Plate", "Flags", "Roads", "Subdivision Names",
  "City Names", "Agriculture", "Soil", "Brand", "Language", "Script", "Road Sign",
  "Electricity Pole", "Bollard", "National Parks", "Biome", "Religion",
];
export const VISIBILITIES = [["visible", "Visible"], ["guide-only", "Guide only"], ["analysis-only", "Analysis only"]];

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
};

// Category = WHAT it is (neutral chip + icon). Location = WHERE it applies (the only color-coded tag).
export const CATEGORY_ICONS = {
  "Architecture": "🏛️", "Phone Numbers": "📞", "Postal Codes": "✉️", "Topography": "⛰️", "Vegetation": "🌿",
  "Infrastructure": "🏗️", "Vehicle": "🚗", "License Plate": "🔢", "Flags": "🚩", "Roads": "🛣️",
  "Subdivision Names": "🗺️", "City Names": "🏙️", "Agriculture": "🌾", "Soil": "🟤", "Brand": "🏷️",
  "Language": "💬", "Script": "🔤", "Road Sign": "🛑", "Electricity Pole": "⚡", "Bollard": "🚧",
  "National Parks": "🏞️", "Biome": "🌲", "Religion": "🛐",
};

const LOCATION_HUES = { Urban: 35, Rural: 140, Anywhere: 220 };
export const tagHue = (t) => LOCATION_HUES[t] ?? 215;
