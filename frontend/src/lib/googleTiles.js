// Standard Google roadmap tiles (labels on, English), no API key. The `pb` string is the one Google's own maps use
// (format taken from github.com/ccmdi/mma, which map-making.app is built on).

const TILE = 256;

export function tileUrl(x, y, zoom) {
  const n = 2 ** zoom;
  if (y < 0 || y >= n) return null; // above / below the world: nothing to draw
  const wx = ((x % n) + n) % n;
  const scale = typeof devicePixelRatio === "number" ? devicePixelRatio : 1;
  const pb = `!1m5!1m4!1i${zoom}!2i${wx}!3i${y}!4i${TILE}!2m2!1e0!2sm!3m12!2sen!3sUS!5e1105`
    + `!12m4!1e68!2m2!1sset!2sRoadmap!12m3!1e37!2m1!1ssmartmaps!5m1!5f${scale}`;
  return `https://maps.googleapis.com/maps/vt?${new URLSearchParams({ pb })}`;
}
