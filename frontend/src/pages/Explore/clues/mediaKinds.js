// What a pasted file / path / link is, and how to turn a Street View link into a picture.

const IMAGE_EXT = /\.(png|jpe?g|webp|gif|bmp|avif|tiff?)$/i;
const GEO_EXT = /\.(geojson|json|gpkg|zip|parquet|kml|shp)$/i;

/** "image" | "svg" | "csv" | "geometry" | null, from a file name (or a path). */
export function kindOfName(name = "") {
  const n = String(name).split(/[?#]/)[0];
  if (/\.svg$/i.test(n)) return "svg";
  if (/\.csv$/i.test(n)) return "csv";
  if (GEO_EXT.test(n)) return "geometry";
  if (IMAGE_EXT.test(n)) return "image";
  return null;
}

/** Same, from a File (falls back to its MIME type when the name says nothing). */
export function kindOfFile(file) {
  const byName = kindOfName(file?.name);
  if (byName) return byName;
  const type = file?.type ?? "";
  if (type === "image/svg+xml") return "svg";
  if (type.startsWith("image/")) return "image";
  if (type === "text/csv") return "csv";
  return null;
}

/** A typed path (Windows drive, rooted Unix path or UNC), as opposed to a URL or other text. */
export const isPathLike = (t) => /^("?)([a-zA-Z]:[\\/]|\/|\\\\)/.test(String(t).trim());

const NUM = String.raw`-?\d+(?:\.\d+)?`;
const num = (v) => (v == null || v === "" || Number.isNaN(Number(v)) ? null : Number(v));
const ASPECT = 16 / 9; // keep equal to PHOTO_ASPECT in src/geo/map_providers.py

// Google Maps URLs carry a HORIZONTAL fov; /api/streetview/photo wants the vertical one.
const verticalFov = (h) => (2 * Math.atan(Math.tan((h * Math.PI) / 360) / ASPECT) * 180) / Math.PI;

/** A Google Maps / Street View link -> { lat, lng, heading, pitch, fov } (fov vertical, may be null).
 *  Returns { short: true } for the shortened maps.app.goo.gl links, whose coordinates are not in the URL. */
export function parseStreetView(text) {
  const s = String(text ?? "").trim();
  if (!/^https?:\/\//i.test(s)) return null;
  let url;
  try { url = new URL(s); } catch { return null; }
  const host = url.hostname.replace(/^www\./, "").toLowerCase();
  if (/^(maps\.app\.goo\.gl|goo\.gl)$/.test(host)) return { short: true };
  if (!/(^|\.)google\.[a-z.]+$/.test(host)) return null;

  const q = url.searchParams;
  const pair = (v) => {
    const m = String(v ?? "").match(new RegExp(`^\\s*(${NUM})\\s*,\\s*(${NUM})\\s*$`));
    return m ? [Number(m[1]), Number(m[2])] : null;
  };
  let lat = null, lng = null, heading = null, pitch = null, fov = null;

  for (const key of ["viewpoint", "cbll", "ll", "q", "destination"]) {
    const p = pair(q.get(key));
    if (p) { [lat, lng] = p; break; }
  }

  // /maps/@lat,lng,3a,75y,191.19h,96.57t/data=...  ->  75y = fov, 191h = heading, 96t = tilt
  const at = url.pathname.match(new RegExp(`/@(${NUM}),(${NUM})(.*)$`));
  if (at) {
    if (lat == null) { lat = Number(at[1]); lng = Number(at[2]); }
    const cam = String(at[3]).match(new RegExp(`^,[\\d.]+a(?:,(${NUM})y)?(?:,(${NUM})h)?(?:,(${NUM})t)?`));
    if (cam) {
      fov = num(cam[1]);
      heading = num(cam[2]);
      const tilt = num(cam[3]);
      pitch = tilt == null ? null : tilt - 90; // 90t = level
    }
  }
  if (lat == null) {
    const d = url.pathname.match(new RegExp(`!3d(${NUM})!4d(${NUM})`));
    if (d) { lat = Number(d[1]); lng = Number(d[2]); }
  }
  if (lat == null || lng == null || Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;

  if (q.has("heading")) heading = num(q.get("heading"));
  if (q.has("pitch")) pitch = num(q.get("pitch"));
  if (q.has("fov")) fov = num(q.get("fov"));

  return {
    lat,
    lng,
    heading: heading ?? 0,
    pitch: Math.max(-90, Math.min(90, pitch ?? 0)),
    fov: fov == null ? null : Math.max(10, Math.min(120, verticalFov(fov))),
  };
}

/** The Street View picture at that spot, as a File ready for the image staging endpoint. */
export async function streetViewShot(view) {
  const params = new URLSearchParams({ lat: view.lat, lng: view.lng, heading: view.heading, pitch: view.pitch });
  if (view.fov != null) params.set("fov", view.fov);
  const r = await fetch(`/api/streetview/photo?${params}`);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  const blob = await r.blob();
  return new File([blob], "streetview.jpg", { type: blob.type || "image/jpeg" });
}
