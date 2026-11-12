// Guesses the category tag of a clue from its text. Returns null when nothing matches.
// Every tag (the camera / coverage ones included) is tried, and the one whose trigger sits closest to the start
// of the text wins; on a tie the camera / coverage tags come first, then the order of RULES (keep in step with
// meta_category / category_for in src/plonkit/categories.py). "Subdivision Names" only counts when nothing else
// matches (it just says where). Every name must exist in GENERAL_TAGS (tags.js).
const SEASON = /\b(snow\w*|winter\w*|summer\w*|spring\w*|autumn\w*|fall)\b/i;
// Each test gives the position of its trigger in the text, or -1.
const at = (re, t) => t.search(re);
const earliest = (...xs) => { const f = xs.filter((i) => i >= 0); return f.length ? Math.min(...f) : -1; };
const both = (a, b, t) => { const i = at(a, t), j = at(b, t); return i < 0 || j < 0 ? -1 : Math.min(i, j); };
const META = [
  ["Google Car", (t) => earliest(at(/\b(google car|street view car|snorkel|roof ?rack|antenn?as?)\b/i, t), both(/\bcar\b/i, /\bback\b/i, t))],
  ["Camera Generation", (t) => at(/\b(camera\s+)?gen(eration)?\.?\s*[1-4]\b/i, t)],
  ["Coverage", (t) => earliest(both(SEASON, /\bcoverage\b/i, t), at(/\btrekkers?\b/i, t))],
  ["Copyright", (t) => at(/(©|\bcopyright\b)/i, t)],
  ["Camera", (t) => at(/\b(smudges?|rifts?|dead pixels?)\b/i, t)],
];
const WEAK = new Set(["Subdivision Names"]);

const RULES = [
  ["License Plate", /\b(licen[sc]e plates?|number plates?|plates?|placas?|matr[ií]culas?)\b/gi],
  ["Phone Numbers", /\b(phone numbers?|telephone|phone|area codes?|tel[eé]fono)\b/gi],
  ["Postal Codes", /\b(postal codes?|post ?codes?|zip codes?|c[oó]digo postal)\b/gi],
  ["Bollard", /\bbollards?\b/gi],
  ["Electricity Pole", /\b(electric(ity)? poles?|utility poles?|power poles?|poles?|power lines?|pylons?)\b/gi],
  ["Road Sign", /\b(road signs?|signs?|signage|se[nñ]al(es)?)\b/gi],
  ["Flags", /\b(flags?|banderas?)\b/gi],
  ["Religion", /\b(religion|religious|church(es)?|mosques?|temples?|cathedrals?|shrines?|cemeter(y|ies)|christian|muslim|orthodox|hindu|buddhis[mt])\b/gi],
  ["Language", /\b(language|spoken|speaks?|dialect|idioma)\b/gi],
  ["Script", /\b(scripts?|alphabets?|cyrillic|arabic|diacritics?|accents?|letters?|characters?)\b/gi],
  ["Brand", /\b(brands?|shops?|stores?|supermarkets?|gas stations?|petrol|logos?|banks?|chains?|marcas?)\b/gi],
  ["City Names", /\b(city names?|town names?|cit(y|ies)|towns?|villages?)\b/gi],
  ["Subdivision Names", /\b(provinces?|subdivisions?|prefectures?|counties|county|districts?|states?|regions? names?)\b/gi],
  ["National Parks", /\b(national parks?|nature reserves?|protected areas?)\b/gi],
  ["Biome", /\b(biomes?|tundra|savann?ahs?|rainforests?|taiga|steppe|jungle)\b/gi],
  ["Animals", /\b(animals?|cows?|cattle|horses?|goats?|sheep|dogs?|storks?|birds?|camels?|donkeys?|llamas?|monkeys?)\b/gi],
  ["Agriculture", /\b(farm(s|ing|land)?|crops?|fields?|agricultur\w*|plantations?|livestock|irrigation)\b/gi],
  ["Soil", /\b(soil|dirt|clay|sandy|laterite|red earth)\b/gi],
  ["Vegetation", /\b(vegetation|trees?|forests?|palms?|pines?|eucalyptus|grass|bushes|shrubs?|plants?|cactus|cacti|sunflowers?|flora)\b/gi],
  ["Landscape", /\b(landscapes?|mountains?|hills?|hilly|flat|plains?|valleys?|volcano(es)?|terrain|elevation|coast(al)?|desert|topography|grassland|lakes?|rocks?|salt pans?)\b/gi],
  ["Architecture", /\b(architecture|buildings?|houses?|roofs?|facades?|walls?|bricks?|concrete|tiles?)\b/gi],
  ["Infrastructure", /\b(infrastructure|bridges?|sewers?|drains?|gutters?|kerbs?|curbs?|sidewalks?|water towers?|wires?|cables?)\b/gi],
  ["Roads", /\b(roads?|asphalt|lanes?|highways?|line markings?|road lines?|cent(re|er) ?lines?|gravel|pavements?)\b/gi],
  ["Vehicle", /\b(vehicles?|cars?|trucks?|buses|bus|taxis?|motorbikes?|scooters?|tuk[- ]?tuks?|bikes?)\b/gi],
];

export function guessCategory(text) {
  const t = String(text ?? "").replace(/https?:\/\/\S+/g, " ");
  const hits = []; // [position, order, tag]
  META.forEach(([tag, find], i) => { const p = find(t); if (p >= 0) hits.push([p, i, tag]); });
  RULES.forEach(([tag, re], i) => { const p = t.search(re); if (p >= 0) hits.push([p, META.length + i, tag]); });
  const strong = hits.filter((h) => !WEAK.has(h[2]));
  const best = (strong.length ? strong : hits).sort((a, b) => a[0] - b[0] || a[1] - b[1])[0];
  return best ? best[2] : null;
}
