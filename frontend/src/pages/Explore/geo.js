// Loads the pre-built map files from /geo (see scripts/build_world_map.py). Each file is fetched once.
const cache = new Map();

function get(url) {
  if (!cache.has(url)) {
    cache.set(url, fetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
        return r.json();
      })
      .catch((e) => {
        cache.delete(url); // let the next visit retry
        throw new Error(`Could not load ${url}. Run "python scripts/build_world_map.py" if the file is missing. (${e.message})`);
      }));
  }
  return cache.get(url);
}

export const loadWorld = () =>
  get("/geo/world.json").then((w) => {
    w.byId ??= Object.fromEntries(w.countries.map((c) => [c.id, c]));
    return w;
  });

export const loadCountryMap = (id) => get(`/geo/admin1/${id}.json`);
