// Canvas renderer for a place (see place.js). Shared by the map explorer and the minigame minimap.
// World coordinates are metres, +y north; view = { s: px per metre, x, y: screen position of the window centre }.

const LAND = "#151b23", SEA = "#1a4562", LAKE = "#1f5578", RAIL = "#8b98a8";
// [colour, width in metres, minimum width in pixels], by road class 0..7 (track ... motorway)
const ROAD = [
  ["#5a4f40", 3, 0.7], ["#566170", 4, 0.8], ["#6f7f93", 5, 1], ["#8a99ac", 6, 1.2],
  ["#b4bfcc", 8, 1.6], ["#e0cf9a", 10, 2.2], ["#f0b46a", 12, 2.6], ["#ff7357", 14, 3],
];
const WATER_LINE = [["#2b6c93", 10, 1.4], ["#2b6c93", 6, 1.2], ["#2b6c93", 2, 0.8]]; // river, canal, stream

export const ALL_LAYERS = { sea: true, water: true, rail: true, roads: true };

export const fitView = (half, w, h) => ({ s: Math.min(w, h) / (half * 2 * 1.04), x: w / 2, y: h / 2 });
export const niceLen = (s) => [10, 20, 50, 100, 200, 500, 1000, 2000, 5000].find((l) => l * s >= 70) ?? 5000;

function trace(g, L, ids, close) {
  const { start, xy } = L;
  g.beginPath();
  const n = ids ? ids.length : L.count;
  for (let k = 0; k < n; k++) {
    const i = ids ? ids[k] : k, a = start[i], b = start[i + 1];
    if (b - a < 2) continue;
    g.moveTo(xy[2 * a], xy[2 * a + 1]);
    for (let j = a + 1; j < b; j++) g.lineTo(xy[2 * j], xy[2 * j + 1]);
    if (close) g.closePath();
  }
}
function stroke(g, L, ids, color, widthM, minPx, s) {
  if (ids && !ids.length) return;
  trace(g, L, ids, false);
  g.strokeStyle = color; g.lineWidth = Math.max(minPx, widthM * s) / s; g.stroke();
}
function fill(g, L, color) {
  if (!L.count) return;
  trace(g, L, null, true);
  g.fillStyle = color; g.fill("evenodd");
}

export function drawPlace(g, place, view, size, dpr, show) {
  const { layers: Ly, meta } = place, half = meta.half_m, s = view.s;
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.fillStyle = "#0a0e13"; g.fillRect(0, 0, size.w, size.h);
  g.setTransform(dpr * s, 0, 0, -dpr * s, dpr * view.x, dpr * view.y);
  g.save();
  g.beginPath(); g.rect(-half, -half, 2 * half, 2 * half); g.clip();
  g.fillStyle = LAND; g.fillRect(-half, -half, 2 * half, 2 * half);
  g.lineCap = "round"; g.lineJoin = "round";
  if (show.sea) { fill(g, Ly.sea, SEA); fill(g, Ly.islands, LAND); }
  if (show.water) {
    fill(g, Ly.water_areas, LAKE);
    Ly.water_lines.groups.forEach((ids, k) => stroke(g, Ly.water_lines, ids, ...WATER_LINE[k], s));
  }
  if (show.rail && Ly.rail.count) {
    g.lineCap = "butt";
    stroke(g, Ly.rail, null, RAIL, 3.5, 2.2, s);
    trace(g, Ly.rail, null, false);
    g.strokeStyle = LAND; g.lineWidth = Math.max(1, 3.5 * s * 0.45) / s; g.setLineDash([9 / s, 9 / s]); g.stroke(); g.setLineDash([]);
    g.lineCap = "round";
  }
  if (show.roads) {
    const [tunnel, normal, bridge] = [Ly.roads.groups[2], Ly.roads.groups[0], Ly.roads.groups[1]];
    g.globalAlpha = 0.4;
    tunnel.forEach((ids, c) => stroke(g, Ly.roads, ids, ROAD[c][0], ROAD[c][1], ROAD[c][2], s));
    g.globalAlpha = 1;
    normal.forEach((ids, c) => stroke(g, Ly.roads, ids, ROAD[c][0], ROAD[c][1], ROAD[c][2], s));
    bridge.forEach((ids, c) => stroke(g, Ly.roads, ids, "#05080c", ROAD[c][1] + 3, ROAD[c][2] + 2.5, s));
    bridge.forEach((ids, c) => stroke(g, Ly.roads, ids, ROAD[c][0], ROAD[c][1], ROAD[c][2], s));
  }
  g.restore();

  // window frame and scale bar (screen space)
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const x0 = view.x - half * s, y0 = view.y - half * s;
  g.strokeStyle = "#3a4656"; g.lineWidth = 1; g.strokeRect(x0, y0, 2 * half * s, 2 * half * s);
  const len = niceLen(s), px = len * s, bx = 16, by = size.h - 18;
  g.strokeStyle = "#e6edf3"; g.lineWidth = 2; g.lineCap = "butt";
  g.beginPath(); g.moveTo(bx, by - 5); g.lineTo(bx, by); g.lineTo(bx + px, by); g.lineTo(bx + px, by - 5); g.stroke();
  g.fillStyle = "#e6edf3"; g.font = "12px 'Bricolage Grotesque', system-ui, sans-serif";
  g.fillText(len >= 1000 ? `${len / 1000} km` : `${len} m`, bx, by - 9);
}
