// Flag image from flagcdn. `code` is an ISO 3166-1 alpha-2 code ("AR", "gb"...).
// Only the width is fixed; the height follows each flag's own proportions so nothing gets cropped.
const WIDTHS = [20, 40, 80, 160, 320];
const pick = (n) => WIDTHS.find((w) => w >= n) ?? WIDTHS[WIDTHS.length - 1];

export default function Flag({ code, width = 24 }) {
  if (!code) return null;
  const c = code.trim().toLowerCase();
  return <img className="flag" alt={code} width={width} loading="lazy"
    src={`https://flagcdn.com/w${pick(width)}/${c}.png`} srcSet={`https://flagcdn.com/w${pick(width * 2)}/${c}.png 2x`}
    style={{ width, height: "auto", objectFit: "contain" }}
    onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} />;
}
