// generate.js: generateCity(params) -> City. A pure function: no DOM, no React, runs in Node or a Worker.
// Pipeline (brief 5.1): normalise -> terrain -> style skeleton -> (later phases: water, highway, ramps, rail,
// intersections, roads) -> validate. A failed attempt is retried with a derived seed; if every attempt fails
// the city is returned with its violations filled in, never hidden.

import { VERSION, defaultBlock, normalizeParams } from "./params.js";
import { createRng, deriveSeed } from "./rng.js";
import { createTerrain } from "./terrain.js";
import { createNetwork } from "./network.js";
import { assembleCity } from "./city.js";
import { hardViolations } from "./validate.js";
import { generateOrganic } from "./organic/generate.js";

const now = () => (typeof performance !== "undefined" ? performance.now() : Date.now());
export const MAX_ATTEMPTS = 3;

export function generateCity(input = {}) {
  const P = normalizeParams(input), t0 = now();
  const params = { ...P, blockSize: P.blockSize ?? defaultBlock(P.style) };
  let best = null;
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    const seed = attempt === 0 ? P.seed : deriveSeed(P.seed, attempt);
    const rng = createRng(seed), terrain = createTerrain(P, rng), net = createNetwork(terrain.bounds);
    let info = {};
    if (P.style === "organic") info = generateOrganic({ P: params, rng, terrain, net });
    const city = assembleCity(net, {
      meta: { version: VERSION, seed: P.seed, usedSeed: seed, attempts: attempt + 1, style: P.style, sizeKm: P.sizeKm, params, info },
      footprint: terrain.polygon,
    });
    if (P.style !== "organic") city.violations.push({ rule: "GEN", message: `the "${P.style}" style is not built yet (phase 3)`, x: 0, y: 0, severity: "error" });
    const bad = hardViolations(city.violations).length;
    if (!best || bad < best.bad) best = { city, bad };
    if (!bad) break;
  }
  best.city.stats.ms = Math.round(now() - t0);
  return best.city;
}
