// index.js: public entry of the city generator package.
// Phase 1 ships the core (network, validator, demo cities). generateCity() arrives with phase 2.

export { DEFAULT_PARAMS, LIMITS, WIDTHS, boundsFor, normalizeParams } from "./params.js";
export { createRng, deriveSeed } from "./rng.js";
export { createNetwork } from "./network.js";
export { assembleCity } from "./city.js";
export { RULE_TEXT, hardViolations, summarize, validate } from "./validate.js";
export { DEMOS, buildDemo } from "./demo.js";
