import { useEffect, useState } from "react";

// Height of an element (e.g. the regional map overlay), kept up to date while it resizes.
export function useBoxHeight(node) {
  const [h, setH] = useState(0);
  useEffect(() => {
    if (!node) { setH(0); return undefined; }
    const read = () => setH(node.offsetHeight);
    read();
    if (typeof ResizeObserver !== "function") return undefined;
    const ro = new ResizeObserver(read);
    ro.observe(node);
    return () => ro.disconnect();
  }, [node]);
  return h;
}

const ROOM = 16; // the overlay sits 8 px from the bottom, so it gets 8 px on top as well

/** Style for the photo box so a regional map taller than the image is never cut off: the box is at least as tall as
 *  the map (plus margin) and the image is centred in it, so the extra room splits above and below the image.
 *  Works with or without a height limit. */
export const photoFit = (overlayH) =>
  (overlayH ? { display: "flex", alignItems: "center", minHeight: Math.ceil(overlayH + ROOM) } : undefined);
