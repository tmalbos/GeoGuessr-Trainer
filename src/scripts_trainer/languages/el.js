/* =====================================================================
   Idioma: Griego
   ---------------------------------------------------------------------
   Este archivo es autocontenido: toda la lógica específica del griego
   (transliteración, tabla de letras, datos de GeoNames a usar) vive acá.
   El core (el .html) no sabe nada de griego; solo llama a lo que este
   archivo registra con registerLanguage().
   ===================================================================== */
(function () {
  "use strict";

  /* Esquema de transliteración basado en ELOT 743, sin acentos.
     Reglas especiales: αι/ει/οι/ου/υι, αυ/ευ (v o f según lo que sigue),
     μπ (b al inicio de palabra, mp en el medio), ντ, γγ, γκ, γξ, γχ. */
  const MAP = {
    "α":"a","β":"v","γ":"g","δ":"d","ε":"e","ζ":"z","η":"i","θ":"th",
    "ι":"i","κ":"k","λ":"l","μ":"m","ν":"n","ξ":"x","ο":"o","π":"p",
    "ρ":"r","σ":"s","ς":"s","τ":"t","υ":"y","φ":"f","χ":"ch","ψ":"ps","ω":"o"
  };
  const DIGRAPHS = {"ου":"ou","γγ":"ng","γξ":"nx","γχ":"nch"};
  const VOICELESS = new Set(["κ","ξ","π","σ","ς","τ","φ","χ","ψ"]);

  function transliterate(text) {
    // Descompone en letras base; los acentos se descartan, la diéresis se recuerda.
    const L = [];
    for (const ch of text.toLowerCase().normalize("NFD")) {
      if (/[\u0300-\u036f]/.test(ch)) {
        if (ch === "\u0308" && L.length) L[L.length - 1].d = true;
        continue;
      }
      L.push({ c: ch, d: false });
    }
    const c = i => (i >= 0 && i < L.length) ? L[i].c : "";
    const isGreek = ch => Object.prototype.hasOwnProperty.call(MAP, ch);

    let out = "";
    for (let i = 0; i < L.length; i++) {
      const a = c(i);
      const b = (L[i + 1] && !L[i + 1].d) ? c(i + 1) : "";
      const pair = a + b;

      if (b) {
        if (pair === "αυ" || pair === "ευ") {
          const nx = c(i + 2);
          out += MAP[a] + ((VOICELESS.has(nx) || !isGreek(nx)) ? "f" : "v");
          i++; continue;
        }
        if (pair === "μπ") {
          out += (i === 0 || !isGreek(c(i - 1))) ? "b" : "mp";
          i++; continue;
        }
        if (Object.prototype.hasOwnProperty.call(DIGRAPHS, pair)) {
          out += DIGRAPHS[pair];
          i++; continue;
        }
      }
      out += isGreek(a) ? MAP[a] : a;
    }
    return out;
  }

  registerLanguage({
    id: "el",
    name: "Griego",
    script: "Alfabeto griego",
    native: "Ελληνικά",
    dir: "ltr",
    fontFamily: '"Noto Sans", "Segoe UI", "Helvetica Neue", Arial, sans-serif',
    wordSize: "clamp(2.6rem, 10vw, 5rem)",
    difficulty: { label: "Muy fácil", level: "muy-facil" },

    lead: "Nombres de localidades de Grecia y Chipre, escritos en griego. Escribí cómo se transliteran.",
    refLead: "Letras del alfabeto griego y su transliteración (esquema basado en ELOT 743; los acentos no se escriben).",

    // Países GeoNames a usar. Cada uno necesita su geonames/XX.txt local.
    countries: ["GR", "CY"],

    // Solo se usan nombres del alfabeto griego moderno (sin politónico antiguo).
    letterRe: /[\u0370-\u03ff]/,

    transliterate,

    pairs: [
      ["Α α","a"],["Β β","v"],["Γ γ","g"],["Δ δ","d"],["Ε ε","e"],["Ζ ζ","z"],
      ["Η η","i"],["Θ θ","th"],["Ι ι","i"],["Κ κ","k"],["Λ λ","l"],["Μ μ","m"],
      ["Ν ν","n"],["Ξ ξ","x"],["Ο ο","o"],["Π π","p"],["Ρ ρ","r"],["Σ σ ς","s"],
      ["Τ τ","t"],["Υ υ","y"],["Φ φ","f"],["Χ χ","ch"],["Ψ ψ","ps"],["Ω ω","o"]
    ],

    extraTitle: "Combinaciones",
    extra: [
      ["ου","ou"],
      ["αυ","av / af según la letra que sigue"],
      ["ευ","ev / ef según la letra que sigue"],
      ["μπ","b al inicio, mp en el medio"],
      ["γγ","ng"],["γξ","nx"],["γχ","nch"],
      ["ά έ ή ί ό ύ ώ","igual que sin acento"]
    ]

    // Este idioma no necesita reference extra (renderExtraReference):
    // no tiene formas posicionales ni nada fuera de letras + combinaciones.
  });
})();
