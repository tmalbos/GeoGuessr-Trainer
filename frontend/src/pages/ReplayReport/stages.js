import { time } from "../../lib/format.js";

// Hues for the stage colors (cool palette) and an emoji to recognise each stage at a glance.
export const STAGES = [
  { id: "clues", label: "Looking for clues", emoji: "🔍", hue: 195 },
  { id: "map", label: "Searching the map", emoji: "🗺️", hue: 235 },
  { id: "pin", label: "Pinpointing", emoji: "📍", hue: 280 },
  { id: "verify", label: "Verifying", emoji: "✅", hue: 150 },
];
export const stageOf = (id) => STAGES.find((s) => s.id === id);

// Chess-style grade of a single event (see replay_grading.py). The icon for each one is
// /grades/<id>.png (frontend/public/grades). Hue colors the small dots on the timeline and the summary chips.
// big = shown as a badge with its icon on the timeline; the rest are small colored dots.
export const GRADES = {
  brilliant: { label: "Brilliant", hue: 180, big: true },
  great: { label: "Great", hue: 215, big: true },
  best: { label: "Best", hue: 140, big: true },
  excellent: { label: "Excellent", hue: 125, big: false },
  good: { label: "Good", hue: 100, big: false },
  inaccuracy: { label: "Inaccuracy", hue: 50, big: true },
  miss: { label: "Miss", hue: 335, big: true },
  mistake: { label: "Mistake", hue: 28, big: true },
  blunder: { label: "Blunder", hue: 4, big: true },
};

export const clock = (ms) => time(Math.round(ms / 1000));
