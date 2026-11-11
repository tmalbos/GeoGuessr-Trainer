// Guesses the category tag of a clue from its text. Returns null when nothing matches.
// The camera / coverage tags come first, in this order, and the first one that matches wins
// (keep in step with meta_category in src/plonkit/categories.py). After that, the tag with the most matches
// wins and order breaks ties: earlier = more specific. Every name must exist in GENERAL_TAGS (tags.js).
const SEASON = /\b(snow\w*|winter\w*|summer\w*|spring\w*|autumn\w*|fall)\b/i;
const META = [
  ["Google Car", (t) => /\b(google car|street view car|snorkel|roof ?rack|antenn?as?)\b/i.test(t)],
  ["Camera Generation", (t) => /\b(camera\s+)?gen(eration)?\.?\s*[1-4]\b/i.test(t)],
  ["Coverage", (t) => SEASON.test(t) && /\bcoverage\b/i.test(t)],
  ["Copyright", (t) => /(©|\bcopyright\b)/i.test(t)],
  ["Camera", (t) => /\b(smudges?|rifts?|dead pixels?)\b/i.test(t)],
];

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
  ["Vegetation", /\b(vegetation|trees?|forests?|palms?|pines?|eucalyptus|grass(land)?|bushes|shrubs?|plants?)\b/gi],
  ["Topography", /\b(mountains?|hills?|hilly|flat|plains?|valleys?|volcano(es)?|terrain|elevation|coast(al)?|desert|topography)\b/gi],
  ["Architecture", /\b(architecture|buildings?|houses?|roofs?|facades?|walls?|brick|concrete|tiles?)\b/gi],
  ["Infrastructure", /\b(infrastructure|bridges?|sewers?|drains?|gutters?|kerbs?|curbs?|sidewalks?|pavements?|water towers?|wires?|cables?)\b/gi],
  ["Roads", /\b(roads?|asphalt|lanes?|highways?|line markings?|road lines?|cent(re|er) ?lines?|gravel)\b/gi],
  ["Vehicle", /\b(vehicles?|cars?|trucks?|buses|bus|taxis?|motorbikes?|scooters?|tuk[- ]?tuks?|bikes?)\b/gi],
];

export function guessCategory(text) {
  const t = String(text ?? "").replace(/https?:\/\/\S+/g, " ");
  const meta = META.find(([, test]) => test(t));
  if (meta) return meta[0];
  let best = null;
  let top = 0;
  for (const [tag, re] of RULES) {
    const n = t.match(re)?.length ?? 0;
    if (n > top) { top = n; best = tag; }
  }
  return best;
}
