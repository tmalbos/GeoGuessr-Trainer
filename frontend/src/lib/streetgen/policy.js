// policy.js: which roads may join or cross which. The rules of the brief (R2-R5, R7) in one place.

import { LIMITS } from "./params.js";

const plain = (e) => !e.highway && !e.ramp;

/**
 * Where may a road END? role: "highway" (the highway end of a ramp), "street" or "any".
 * Highways never end on anything (they run border to border). Highway and ramp edges are never joined by others.
 */
export function targetRules(road, role = "any") {
  if (road.highway) return { node: () => false, edge: () => false };
  if (role === "highway") return { node: () => false, edge: (e) => e.highway && !e.bridge && e.layer === 0 };
  const okCls = road.ramp || road.cls === "secondary" ? (c) => c !== "tertiary"
    : road.cls === "primary" ? (c) => c === "primary" : () => true;
  return {
    node: (edges) => edges.length > 0 && edges.every(plain) && edges.some((e) => okCls(e.cls)),
    edge: (e) => plain(e) && e.layer === 0 && !e.bridge && okCls(e.cls),
  };
}

const no = (why) => ({ kind: "forbidden", why });

/** What happens where `road` crosses the existing edge `e`: a junction, an overpass, or not allowed. */
export function crossingKind(road, e) {
  if (e.layer !== 0 || e.bridge) return no("it would cross a bridge deck");
  if (road.ramp || e.ramp) return no("ramps cannot cross other roads");
  if (road.highway && e.highway) return no("highways cannot cross each other");
  if (road.highway) return e.cls === "tertiary" ? no("a highway cannot cross a tertiary street") : { kind: "overpass", over: "existing", underWidth: road.width };
  if (e.highway) return road.cls === "tertiary" ? no("a tertiary street cannot cross a highway") : { kind: "overpass", over: "new", underWidth: e.width };
  return { kind: "junction" };
}

/** Half the length of the bridge deck needed to span a road of width w. Never shorter than the min edge. */
export const bridgeHalf = (underWidth) => Math.max(LIMITS.minEdge / 2, underWidth / 2 + LIMITS.bridgeClearance);
