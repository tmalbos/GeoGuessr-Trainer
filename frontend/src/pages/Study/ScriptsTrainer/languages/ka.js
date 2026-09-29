/* Georgiano (mjedruli) — Sistema Nacional de Georgia (2002).
   Las eyectivas (კ პ ტ ყ წ ჭ) llevan apóstrofo. */

const MAP = {
  "ა":"a","ბ":"b","გ":"g","დ":"d","ე":"e","ვ":"v","ზ":"z","თ":"t",
  "ი":"i","კ":"k'","ლ":"l","მ":"m","ნ":"n","ო":"o","პ":"p'","ჟ":"zh",
  "რ":"r","ს":"s","ტ":"t'","უ":"u","ფ":"p","ქ":"k","ღ":"gh","ყ":"q'",
  "შ":"sh","ჩ":"ch","ც":"ts","ძ":"dz","წ":"ts'","ჭ":"ch'","ხ":"kh",
  "ჯ":"j","ჰ":"h"
};

function transliterate(text) {
  let out = "";
  for (const ch of text.normalize("NFC")) {
    out += Object.prototype.hasOwnProperty.call(MAP, ch) ? MAP[ch] : ch;
  }
  return out;
}

// El apóstrofo de las eyectivas es opcional al responder.
function normalizeAnswer(s) {
  return s.trim().toLowerCase()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/['’]/g, "")
    .replace(/[^a-z0-9 ]/g, "")
    .replace(/\s+/g, " ");
}

export default {
  id: "ka",
  name: "Georgiano",
  script: "Alfabeto mjedruli",
  native: "ქართული",
  dir: "ltr",
  fontFamily: '"Noto Sans Georgian", "Sylfaen", "Noto Sans", "Segoe UI", sans-serif',
  wordSize: "clamp(2.6rem, 10vw, 5rem)",
  difficulty: { label: "Fácil", level: "facil" },

  lead: "Nombres de localidades de Georgia, escritos en el alfabeto mjedruli. Escribí cómo se transliteran.",
  refLead: "Letras del alfabeto georgiano y su transliteración (Sistema Nacional de Georgia, 2002). No tiene mayúsculas ni signos diacríticos.",

  countries: ["GE"],
  letterRe: /[\u10a0-\u10ff]/,
  transliterate,
  normalizeAnswer,

  pairs: [
    ["ა","a"],["ბ","b"],["გ","g"],["დ","d"],["ე","e"],["ვ","v"],["ზ","z"],
    ["თ","t"],["ი","i"],["კ","k'"],["ლ","l"],["მ","m"],["ნ","n"],["ო","o"],
    ["პ","p'"],["ჟ","zh"],["რ","r"],["ს","s"],["ტ","t'"],["უ","u"],["ფ","p"],
    ["ქ","k"],["ღ","gh"],["ყ","q'"],["შ","sh"],["ჩ","ch"],["ც","ts"],
    ["ძ","dz"],["წ","ts'"],["ჭ","ch'"],["ხ","kh"],["ჯ","j"],["ჰ","h"]
  ],

  extraTitle: "",
  extra: []
};
