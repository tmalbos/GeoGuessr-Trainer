export function defaultNormalizeAnswer(s) {
  return s.trim().toLowerCase()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9' ]/g, "")
    .replace(/\s+/g, " ");
}

function isPureScript(s, re) {
  let has = false;
  for (const ch of s) {
    if (re.test(ch)) { has = true; continue; }
    if (/[0-9_]/.test(ch)) return false;
    if (/\p{L}/u.test(ch)) return false;
  }
  return has;
}

function pickName(name, alternates, re) {
  if (isPureScript(name, re)) return name;
  if (!alternates) return "";
  for (const candidate of alternates.split(",")) {
    const s = candidate.trim();
    if (s && isPureScript(s, re)) return s;
  }
  return "";
}

function collectLine(line, lang, code, acc) {
  if (!line || line.indexOf("\tP\t") === -1) return;
  const f = line.split("\t");
  if (f.length < 19 || f[6] !== "P") return;
  const name = pickName(f[1], f[3], lang.letterRe);
  if (!name) return;
  const key = `${code}|${name}`;
  if (acc.seen.has(key)) return;
  acc.seen.add(key);
  acc.list.push({
    id: f[0], countryCode: code, name,
    transliteration: lang.transliterate(name),
    featureCode: f[7], population: Number(f[14]) || 0,
  });
}

async function streamLines(stream, onLine) {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  let rest = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    rest += decoder.decode(value, { stream: true });
    let start = 0, idx;
    while ((idx = rest.indexOf("\n", start)) !== -1) {
      onLine(rest.slice(start, idx));
      start = idx + 1;
    }
    rest = rest.slice(start);
  }
  rest += decoder.decode();
  if (rest) onLine(rest);
}

/** onProgress({ code, index, total }) fires as each country file starts loading. */
export async function loadPlaces(lang, onProgress) {
  const acc = { list: [], seen: new Set() };
  let loaded = 0;
  const total = lang.countries.length;
  for (const [index, code] of lang.countries.entries()) {
    onProgress?.({ code, index, total });
    try {
      const r = await fetch(`/api/study/scripts/geonames/${code}`);
      if (!r.ok) throw new Error(`${code}.txt: HTTP ${r.status}`);
      const onLine = (l) => collectLine(l, lang, code, acc);
      if (r.body) await streamLines(r.body, onLine);
      else for (const l of (await r.text()).split(/\r?\n/)) onLine(l);
      loaded++;
    } catch { /* try the next country; we only fail if none load */ }
  }
  if (!loaded) throw new Error("Couldn't load the place names. Check that the GeoNames files are in data/study/scripts/geonames/.");
  const map = new Map();
  for (const p of acc.list) map.set(`${p.countryCode}|${p.name}`, p);
  const places = [...map.values()];
  if (!places.length) throw new Error("No usable place names found for this language.");
  return places;
}
