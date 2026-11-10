// Guesses the category tag of a clue from its text. Returns null when nothing matches.
// Order breaks ties: earlier = more specific. Every name must exist in GENERAL_TAGS (tags.js).
const RULES = [
  ["License Plate", /\b(licen[sc]e plates?|number plates?|plates?|placas?|matr[ií]culas?)\b/gi],
  ["Phone Numbers", /\b(phone numbers?|telephone|phone|area codes?|tel[eé]fono)\b/gi],
  ["Postal Codes", /\b(postal codes?|post ?codes?|zip codes?|c[oó]digo postal)\b/gi],
  ["Bollard", /\bbollards?\b/gi],
  ["Electricity Pole", /\b(electric(ity)? poles?|utility poles?|power poles?|poles?|power lines?|pylons?)\b/gi],
  ["Road Sign", /\b(road signs?|signs?|signage|se[nñ]al(es)?)\b/gi],
  ["Flags", /\b(flags?|banderas?)\b/gi],
  ["Car Meta", /\b(car meta|google car|snorkel|roof rack|antenna|car blur)\b/gi],
  ["Generation Meta", /\b(gen(eration)? ?[1-4]|generations?|trekker)\b/gi],
  ["Camera Meta", /\b(camera|low[- ]?cam|small cam|lens|blurry|blur)\b/gi],
  ["Copyright", /(©|\bcopyright\b)/gi],
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
  let best = null;
  let top = 0;
  for (const [tag, re] of RULES) {
    const n = t.match(re)?.length ?? 0;
    if (n > top) { top = n; best = tag; }
  }
  return best;
}
