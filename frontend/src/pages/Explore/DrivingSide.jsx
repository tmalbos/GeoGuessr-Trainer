// Top-down road, one arrow per lane. Cars going "up" keep to their own side:
// right-hand traffic -> the up arrow is in the right lane; left-hand traffic -> in the left lane.
// Bright cyan = the lane you drive in, coral = the oncoming lane.
const LEFT_X = 11;
const RIGHT_X = 25;
const MINE = "#7fd8ff";
const ONCOMING = "#ff7357";

const up = (x) => `M${x} 8L${x + 5} 18H${x + 2.2}V36H${x - 2.2}V18H${x - 5}Z`;
const down = (x) => `M${x} 36L${x + 5} 26H${x + 2.2}V8H${x - 2.2}V26H${x - 5}Z`;

export default function DrivingSide({ side }) {
  if (side !== "L" && side !== "R") return null;
  const right = side === "R";
  const label = right ? "Drives on the right" : "Drives on the left";
  const mineX = right ? RIGHT_X : LEFT_X;
  const oncomingX = right ? LEFT_X : RIGHT_X;
  return <svg className="cp-road" width="36" height="44" viewBox="0 0 36 44" role="img" aria-label={label}>
    <title>{label}</title>
    <rect x="0.5" y="0.5" width="35" height="43" rx="6" fill="#262d37" stroke="#46525f" />
    <rect x={mineX - 7} y="1" width="14" height="42" fill={MINE} opacity=".1" />
    <path d="M4 3V41M32 3V41" stroke="#e6edf3" strokeOpacity=".85" strokeWidth="1.4" strokeLinecap="round" />
    <path d="M18 3V41" stroke="#ffd04d" strokeWidth="1.8" strokeDasharray="5 4" />
    <path d={up(mineX)} fill={MINE} />
    <path d={down(oncomingX)} fill={ONCOMING} />
  </svg>;
}
