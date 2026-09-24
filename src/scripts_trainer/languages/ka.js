/* =====================================================================
   Idioma: Georgiano (alfabeto mjedruli)
   ---------------------------------------------------------------------
   Autocontenido: transliteración, alfabeto y qué país de GeoNames usar.
   El georgiano no tiene mayúsculas/minúsculas, ni diacríticos, ni
   ligaduras posicionales: cada letra se lee siempre igual, así que la
   transliteración es un mapeo directo letra→sonido, sin reglas extra.
   ===================================================================== */
(function () {
  "use strict";

  /* Sistema Nacional de Georgia (2002), el estándar oficial de
     romanización. Las consonantes eyectivas (კ პ ტ ყ წ ჭ) llevan
     apóstrofo; sus contrapartes aspiradas (ქ ფ თ ხ ც ჩ) no. */
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

  /* El comparador por defecto del core ya sirve (solo compara ascii en
     minúsculas), pero el apóstrofo de las eyectivas conviene tratarlo
     como opcional: si alguien escribe "k" en vez de "k'" para კ, que
     igual cuente como correcto, porque en la práctica casi nadie lo
     tipea y no cambia qué letra se identificó. */
  function normalizeAnswer(s) {
    return s.trim().toLowerCase()
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .replace(/['’]/g, "")
      .replace(/[^a-z0-9 ]/g, "")
      .replace(/\s+/g, " ");
  }

  registerLanguage({
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

    // Archivo local necesario: geonames/GE.txt
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

    // Sin renderExtraReference: no hay nada posicional que agregar.
  });
})();
