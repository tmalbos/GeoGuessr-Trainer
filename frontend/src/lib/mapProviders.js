import { api } from "./api.js";

// Where the map tiles come from. Built in: OpenStreetMap (no key) and Google with the user's own key
// (the backend creates the tile session and hands over a URL template, see /api/settings/map).
//
// Local plug-ins: any frontend/src/private/*.js with a default export
//   { id, label, tileUrl(x, y, z), attribution, maxZoom? }
// That folder does not exist in the public repo; the glob then finds nothing.
const found = import.meta.glob("../private/*.js", { eager: true });
const LOCAL = Object.values(found)
  .map((m) => m.default)
  .filter((p) => p?.id && typeof p.tileUrl === "function");

const wrapX = (x, z) => { const n = 2 ** z; return ((x % n) + n) % n; };
const inRange = (y, z) => y >= 0 && y < 2 ** z;

const OSM = {
  id: "osm",
  label: "OpenStreetMap (free, no key)",
  attribution: "© OpenStreetMap contributors",
  maxZoom: 19,
  tileUrl: (x, y, z) => (inRange(y, z) ? `https://tile.openstreetmap.org/${z}/${wrapX(x, z)}/${y}.png` : null),
};

const google = (template) => ({
  id: "google",
  label: "Google Maps (your API key)",
  attribution: "Map data © Google",
  maxZoom: 22,
  tileUrl: (x, y, z) => (inRange(y, z)
    ? template.replace("{z}", String(z)).replace("{x}", String(wrapX(x, z))).replace("{y}", String(y))
    : null),
});

let config = null;
let current = null;

const pick = (c) => {
  if (c.provider === "osm") return OSM;
  if (c.provider === "google") {
    // The only fallback: a missing/rejected Google key must not lock the user out of the app.
    return c.tile_template ? google(c.tile_template) : OSM;
  }
  const local = LOCAL.find((p) => p.id === c.provider);
  if (!local) throw new Error(`Unknown map source "${c.provider}".`);
  return local;
};

/** Call once before the first map is drawn. Throws when the setting can't be read or used. */
export async function initMapProvider() {
  config = await api("/settings/map");
  current = pick(config);
  return config;
}

export const tileUrl = (x, y, z) => current.tileUrl(x, y, z);
export const maxTileZoom = () => current.maxZoom ?? 21;
export const attribution = () => current.attribution;
export const streetViewAvailable = () => !!config.street_view;
export const mapConfig = () => config;

export const providerOptions = () => [
  { id: OSM.id, label: OSM.label },
  { id: "google", label: "Google Maps (your API key)" },
  ...LOCAL.map((p) => ({ id: p.id, label: p.label ?? p.id })),
];

/** body: { provider, google_key? }. google_key: omit = keep, "" = remove. */
export const saveMapSettings = (body) => api("/settings/map", { method: "PUT", body });
