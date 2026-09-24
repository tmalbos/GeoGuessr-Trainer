/* =====================================================================
   Idioma: Árabe
   ---------------------------------------------------------------------
   Autocontenido: transliteración, alfabeto, formas según la posición
   (exclusivas del árabe) y el comparador de respuestas propio (porque
   la transliteración usa símbolos que el comparador genérico no
   necesita conocer, como ʿ ʾ ḥ ṣ ḍ ṭ ẓ).
   ===================================================================== */
(function () {
  "use strict";

  /* Mapa de transliteración propio (la salida sale de acá, no del dataset). */
  const MAP = {
    "ا":"ā","أ":"ʾa","إ":"ʾi","آ":"ʾā","ٱ":"a",
    "ب":"b","ت":"t","ث":"th","ج":"j","ح":"ḥ","خ":"kh",
    "د":"d","ذ":"dh","ر":"r","ز":"z","س":"s","ش":"sh",
    "ص":"ṣ","ض":"ḍ","ط":"ṭ","ظ":"ẓ","ع":"ʿ","غ":"gh",
    "ف":"f","ق":"q","ك":"k","ل":"l","م":"m","ن":"n",
    "ه":"h","و":"w","ي":"y","ى":"ā","ء":"ʾ","ؤ":"ʾw","ئ":"ʾy",
    "ة":"h","ﻻ":"lā","لا":"lā",
    "َ":"a","ِ":"i","ُ":"u","ً":"an","ٍ":"in","ٌ":"un","ْ":"",
    "ّ":"", "ـ":""
  };

  function transliterate(text) {
    let out = "";
    for (const ch of text.normalize("NFC")) {
      out += Object.prototype.hasOwnProperty.call(MAP, ch) ? MAP[ch] : ch;
    }
    return out;
  }

  /* La transliteración árabe generalmente no lleva vocales breves porque
     el propio nombre de GeoNames no las trae (el árabe estándar sin
     vocalizar es así); reponerlas bien requeriría reconocer la palabra,
     no solo la letra, así que no se inventan acá. */
  function normalizeAnswer(s) {
    return s.trim().toLowerCase()
      .normalize("NFD").replace(/[\u0300-\u036f]/g, "")
      .replace(/ʿ/g, "3").replace(/ʾ/g, "'")
      .replace(/ḥ/g, "h").replace(/ṣ/g, "s")
      .replace(/ḍ/g, "d").replace(/ṭ/g, "t").replace(/ẓ/g, "z")
      .replace(/ā/g, "a").replace(/ī/g, "i").replace(/ū/g, "u")
      .replace(/[’']/g, "'")
      .replace(/[^a-z0-9' ]/g, "")
      .replace(/\s+/g, " ");
  }

  /* Formas de cada letra según la posición en la palabra, usando los
     caracteres dedicados del bloque Unicode "Arabic Presentation Forms-B"
     (se ve la forma correcta con cualquier fuente, sin depender de que el
     navegador conecte los glifos). Las letras que no se conectan hacia
     adelante (ا د ذ ر ز و ة ى y las variantes de hamza salvo ئ) solo
     tienen forma aislada/final. */
  const FORMS = {
    "ء": { iso: "\uFE80" },
    "آ": { iso: "\uFE81", fin: "\uFE82" },
    "أ": { iso: "\uFE83", fin: "\uFE84" },
    "ؤ": { iso: "\uFE85", fin: "\uFE86" },
    "إ": { iso: "\uFE87", fin: "\uFE88" },
    "ئ": { iso: "\uFE89", fin: "\uFE8A", init: "\uFE8B", med: "\uFE8C" },
    "ا": { iso: "\uFE8D", fin: "\uFE8E" },
    "ب": { iso: "\uFE8F", fin: "\uFE90", init: "\uFE91", med: "\uFE92" },
    "ة": { iso: "\uFE93", fin: "\uFE94" },
    "ت": { iso: "\uFE95", fin: "\uFE96", init: "\uFE97", med: "\uFE98" },
    "ث": { iso: "\uFE99", fin: "\uFE9A", init: "\uFE9B", med: "\uFE9C" },
    "ج": { iso: "\uFE9D", fin: "\uFE9E", init: "\uFE9F", med: "\uFEA0" },
    "ح": { iso: "\uFEA1", fin: "\uFEA2", init: "\uFEA3", med: "\uFEA4" },
    "خ": { iso: "\uFEA5", fin: "\uFEA6", init: "\uFEA7", med: "\uFEA8" },
    "د": { iso: "\uFEA9", fin: "\uFEAA" },
    "ذ": { iso: "\uFEAB", fin: "\uFEAC" },
    "ر": { iso: "\uFEAD", fin: "\uFEAE" },
    "ز": { iso: "\uFEAF", fin: "\uFEB0" },
    "س": { iso: "\uFEB1", fin: "\uFEB2", init: "\uFEB3", med: "\uFEB4" },
    "ش": { iso: "\uFEB5", fin: "\uFEB6", init: "\uFEB7", med: "\uFEB8" },
    "ص": { iso: "\uFEB9", fin: "\uFEBA", init: "\uFEBB", med: "\uFEBC" },
    "ض": { iso: "\uFEBD", fin: "\uFEBE", init: "\uFEBF", med: "\uFEC0" },
    "ط": { iso: "\uFEC1", fin: "\uFEC2", init: "\uFEC3", med: "\uFEC4" },
    "ظ": { iso: "\uFEC5", fin: "\uFEC6", init: "\uFEC7", med: "\uFEC8" },
    "ع": { iso: "\uFEC9", fin: "\uFECA", init: "\uFECB", med: "\uFECC" },
    "غ": { iso: "\uFECD", fin: "\uFECE", init: "\uFECF", med: "\uFED0" },
    "ف": { iso: "\uFED1", fin: "\uFED2", init: "\uFED3", med: "\uFED4" },
    "ق": { iso: "\uFED5", fin: "\uFED6", init: "\uFED7", med: "\uFED8" },
    "ك": { iso: "\uFED9", fin: "\uFEDA", init: "\uFEDB", med: "\uFEDC" },
    "ل": { iso: "\uFEDD", fin: "\uFEDE", init: "\uFEDF", med: "\uFEE0" },
    "م": { iso: "\uFEE1", fin: "\uFEE2", init: "\uFEE3", med: "\uFEE4" },
    "ن": { iso: "\uFEE5", fin: "\uFEE6", init: "\uFEE7", med: "\uFEE8" },
    "ه": { iso: "\uFEE9", fin: "\uFEEA", init: "\uFEEB", med: "\uFEEC" },
    "و": { iso: "\uFEED", fin: "\uFEEE" },
    "ى": { iso: "\uFEEF", fin: "\uFEF0" },
    "ي": { iso: "\uFEF1", fin: "\uFEF2", init: "\uFEF3", med: "\uFEF4" }
  };
  const FORMS_ORDER = [
    "ا","ب","ت","ث","ج","ح","خ","د","ذ","ر","ز","س","ش","ص","ض",
    "ط","ظ","ع","غ","ف","ق","ك","ل","م","ن","ه","و","ي",
    "ء","أ","إ","آ","ؤ","ئ","ة"
  ];

  /* Hook exclusivo del árabe: agrega la tabla de formas posicionales
     a la pantalla de referencia. El core solo llama a esta función y
     le pasa un contenedor vacío; no sabe nada de su contenido. */
  function renderExtraReference(container) {
    const rows = FORMS_ORDER.map(letter => {
      const f = FORMS[letter];
      const cell = glyph => glyph
        ? `<td class="g" dir="rtl">${glyph}</td>`
        : `<td class="na">—</td>`;
      return `<tr><td class="g" dir="rtl">${letter}</td>` +
        cell(f.iso) + cell(f.init) + cell(f.med) + cell(f.fin) + `</tr>`;
    }).join("");

    container.innerHTML =
      `<h2>Formas según la posición</h2>
       <p class="lead" style="margin:0 0 .8rem">
         La misma letra se escribe distinto según vaya sola, al principio,
         en medio o al final de la palabra. Donde dice “—”, esa letra no
         se conecta con la que sigue (aunque la anterior sí se conecte
         con ella).
       </p>
       <table class="pos-table">
         <thead><tr><th>Letra</th><th>Aislada</th><th>Inicial</th><th>Medial</th><th>Final</th></tr></thead>
         <tbody>${rows}</tbody>
       </table>`;
  }

  registerLanguage({
    id: "ar",
    name: "Árabe",
    script: "Alfabeto árabe",
    native: "العربية",
    dir: "rtl",
    fontFamily: '"Amiri", "Noto Naskh Arabic", "Scheherazade New", "Geeza Pro", "Segoe UI", "Traditional Arabic", serif',
    wordSize: "clamp(3.4rem, 14vw, 7.5rem)",
    difficulty: { label: "Media", level: "media" },

    lead: "Nombres de localidades de países árabes, escritos en árabe. Escribí cómo se transliteran.",
    refLead: "Letras del alfabeto árabe y su transliteración. La vocalización breve casi nunca está en el nombre original, así que no se inventa.",

    countries: ["AE", "OM", "KW", "TN", "JO"],

    letterRe: /[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff]/,

    transliterate,
    normalizeAnswer,

    pairs: [
      ["ا","ā"],["ب","b"],["ت","t"],["ث","th"],["ج","j"],["ح","ḥ"],
      ["خ","kh"],["د","d"],["ذ","dh"],["ر","r"],["ز","z"],["س","s"],
      ["ش","sh"],["ص","ṣ"],["ض","ḍ"],["ط","ṭ"],["ظ","ẓ"],["ع","ʿ"],
      ["غ","gh"],["ف","f"],["ق","q"],["ك","k"],["ل","l"],["م","m"],
      ["ن","n"],["ه","h"],["و","w"],["ي","y"],["ء","ʾ"]
    ],

    extraTitle: "Signos",
    extra: [
      ["َ","a"],["ِ","i"],["ُ","u"],["ْ","sin vocal"],
      ["ّ","dobla la consonante"],["ً","an"],["ٍ","in"],["ٌ","un"]
    ],

    renderExtraReference
  });
})();
