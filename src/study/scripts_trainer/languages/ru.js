/* =====================================================================
   Idioma: Ruso (cirílico)
   ---------------------------------------------------------------------
   Autocontenido: transliteración, alfabeto y qué países de GeoNames usar.
   ===================================================================== */
(function () {
  "use strict";

  /* Esquema simplificado tipo BGN/PCGN. */
  const MAP = {
    "а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"yo","ж":"zh","з":"z",
    "и":"i","й":"y","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r",
    "с":"s","т":"t","у":"u","ф":"f","х":"kh","ц":"ts","ч":"ch","ш":"sh",
    "щ":"shch","ъ":"","ы":"y","ь":"","э":"e","ю":"yu","я":"ya"
  };

  function transliterate(text) {
    let out = "";
    for (const ch of text.toLowerCase().normalize("NFC")) {
      out += Object.prototype.hasOwnProperty.call(MAP, ch) ? MAP[ch] : ch;
    }
    return out;
  }

  registerLanguage({
    id: "ru",
    name: "Ruso",
    script: "Alfabeto cirílico",
    native: "Русский",
    dir: "ltr",
    fontFamily: '"Noto Sans", "Segoe UI", "Helvetica Neue", Arial, sans-serif',
    wordSize: "clamp(2.6rem, 10vw, 5rem)",
    difficulty: { label: "Muy fácil", level: "muy-facil" },

    lead: "Nombres de localidades de Rusia, escritos en cirílico. Escribí cómo se transliteran.",
    refLead: "Letras del alfabeto cirílico ruso y su transliteración (esquema simplificado tipo BGN/PCGN).",

    // Archivo local necesario: geonames/RU.txt (es grande; se lee en streaming).
    countries: ["BG", "RU"],

    letterRe: /[\u0410-\u044f\u0401\u0451]/,

    transliterate,

    pairs: [
      ["А а","a"],["Б б","b"],["В в","v"],["Г г","g"],["Д д","d"],["Е е","e"],
      ["Ё ё","yo"],["Ж ж","zh"],["З з","z"],["И и","i"],["Й й","y"],["К к","k"],
      ["Л л","l"],["М м","m"],["Н н","n"],["О о","o"],["П п","p"],["Р р","r"],
      ["С с","s"],["Т т","t"],["У у","u"],["Ф ф","f"],["Х х","kh"],["Ц ц","ts"],
      ["Ч ч","ch"],["Ш ш","sh"],["Щ щ","shch"],["Ъ ъ","no se escribe"],
      ["Ы ы","y"],["Ь ь","no se escribe"],["Э э","e"],["Ю ю","yu"],["Я я","ya"]
    ],

    extraTitle: "",
    extra: []

    // Sin renderExtraReference: el ruso no tiene formas posicionales.
  });
})();
