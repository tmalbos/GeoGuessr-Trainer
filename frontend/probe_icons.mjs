import { renderToStaticMarkup } from "react-dom/server";
import { CATEGORY_ICONS } from "./src/pages/Explore/clues/tags.js";

const out = {};
for (const [k, v] of Object.entries(CATEGORY_ICONS)) {
  if (typeof v === "string") continue;
  out[k] = renderToStaticMarkup(v);
}
console.log(JSON.stringify(out));
