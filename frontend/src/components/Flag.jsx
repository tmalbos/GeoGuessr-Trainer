// Flag image from flagcdn. `code` is an ISO 3166-1 alpha-2 code ("AR", "gb"...).
const WIDTHS = [20, 40, 80, 160, 320];
const pick = (n) => WIDTHS.find((w) => w >= n) ?? WIDTHS[WIDTHS.length - 1];

export default function Flag({ code, width = 24 }) {
  if (!code) return null;
  const c = code.trim().toLowerCase();
  return <img className="flag" alt={code} width={width} height={Math.round(width * 0.75)} loading="lazy"
    src={`https://flagcdn.com/w${pick(width)}/${c}.png`} srcSet={`https://flagcdn.com/w${pick(width * 2)}/${c}.png 2x`}
    style={{ width, height: Math.round(width * 0.75) }}
    onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} />;
}
