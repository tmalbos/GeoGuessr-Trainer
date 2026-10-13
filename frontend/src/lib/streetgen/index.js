// index.js: public entry of the city generator package.
// Phase 1: core (network, validator, demo cities). Phase 2: generateCity() with the organic style.

export { DEFAULT_PARAMS, LIMITS, WIDTHS, boundsFor, normalizeParams } from "./params.js";
export { createRng, deriveSeed } from "./rng.js";
export { createNetwork } from "./network.js";
export { assembleCity } from "./city.js";
export { RULE_TEXT, hardViolations, summarize, validate } from "./validate.js";
export { DEMOS, buildDemo } from "./demo.js";
export { MAX_ATTEMPTS, generateCity } from "./generate.js";
