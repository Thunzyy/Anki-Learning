/** ISO 639 (and common names) → ISO 3166-1 alpha-2 for a default flag. */
const LANG_TO_CC = {
  aa: "dj", ab: "ge", ae: "ir", af: "za", ak: "gh", am: "et", an: "es", ar: "sa",
  as: "in", av: "ru", ay: "bo", az: "az", ba: "ru", be: "by", bg: "bg", bh: "in",
  bi: "vu", bm: "ml", bn: "bd", bo: "cn", br: "fr", bs: "ba", ca: "es", ce: "ru",
  ch: "gu", co: "fr", cr: "ca", cs: "cz", cu: "ru", cv: "ru", cy: "gb", da: "dk",
  de: "de", dv: "mv", dz: "bt", ee: "gh", el: "gr", en: "us", eo: "eu", es: "es",
  et: "ee", eu: "es", fa: "ir", ff: "sn", fi: "fi", fj: "fj", fo: "fo", fr: "fr",
  fy: "nl", ga: "ie", gd: "gb", gl: "es", gn: "py", gu: "in", gv: "im", ha: "ng",
  he: "il", hi: "in", ho: "pg", hr: "hr", ht: "ht", hu: "hu", hy: "am", hz: "na",
  ia: "un", id: "id", ie: "un", ig: "ng", ii: "cn", ik: "us", io: "un", is: "is",
  it: "it", iu: "ca", ja: "jp", jv: "id", ka: "ge", kg: "cd", ki: "ke", kj: "ao",
  kk: "kz", kl: "gl", km: "kh", kn: "in", ko: "kr", kr: "ne", ks: "in", ku: "iq",
  kv: "ru", kw: "gb", ky: "kg", la: "va", lb: "lu", lg: "ug", li: "nl", ln: "cd",
  lo: "la", lt: "lt", lu: "cd", lv: "lv", mg: "mg", mh: "mh", mi: "nz", mk: "mk",
  ml: "in", mn: "mn", mr: "in", ms: "my", mt: "mt", my: "mm", na: "nr", nb: "no",
  nd: "zw", ne: "np", ng: "na", nl: "nl", nn: "no", no: "no", nr: "za", nv: "us",
  ny: "mw", oc: "fr", oj: "ca", om: "et", or: "in", os: "ge", pa: "in", pi: "in",
  pl: "pl", ps: "af", pt: "br", qu: "pe", rm: "ch", rn: "bi", ro: "ro", ru: "ru",
  rw: "rw", sa: "in", sc: "it", sd: "pk", se: "no", sg: "cf", si: "lk", sk: "sk",
  sl: "si", sm: "ws", sn: "zw", so: "so", sq: "al", sr: "rs", ss: "sz", st: "ls",
  su: "id", sv: "se", sw: "tz", ta: "in", te: "in", tg: "tj", th: "th", ti: "er",
  tk: "tm", tl: "ph", tn: "bw", to: "to", tr: "tr", ts: "za", tt: "ru", tw: "gh",
  ty: "pf", ug: "cn", uk: "ua", ur: "pk", uz: "uz", ve: "za", vi: "vn", vo: "un",
  wa: "be", wo: "sn", xh: "za", yi: "il", yo: "ng", za: "cn", zh: "cn", zu: "za",
  "pt-pt": "pt", "pt-br": "br", "zh-cn": "cn", "zh-tw": "tw", "zh-hk": "hk",
  "en-gb": "gb", "en-us": "us", "en-au": "au", "es-mx": "mx", "es-ar": "ar",
  "fr-ca": "ca", "fr-be": "be", "nl-be": "be", "sv-fi": "fi",
} as const;

const NAMES = {
  english: "en", anglais: "en",
  french: "fr", français: "fr", francais: "fr",
  german: "de", allemand: "de", deutsch: "de",
  spanish: "es", espagnol: "es", español: "es", espanol: "es",
  portuguese: "pt", portugais: "pt", português: "pt", portugues: "pt",
  "portuguese (brazil)": "pt", "portugais (brésil)": "pt", "portugais (bresil)": "pt",
  "portuguese (portugal)": "pt-pt", "portugais (portugal)": "pt-pt",
  italian: "it", italien: "it",
  japanese: "ja", japonais: "ja",
  chinese: "zh", chinois: "zh", "chinois (mandarin)": "zh",
  korean: "ko", coréen: "ko", coreen: "ko",
  arabic: "ar", arabe: "ar",
  russian: "ru", russe: "ru",
  dutch: "nl", néerlandais: "nl", neerlandais: "nl",
  swedish: "sv", suédois: "sv", suedois: "sv",
  norwegian: "no", norvégien: "no", norvegien: "no",
  danish: "da", danois: "da",
  finnish: "fi", finnois: "fi",
  polish: "pl", polonais: "pl",
  czech: "cs", tchèque: "cs", tcheque: "cs",
  greek: "el", grec: "el",
  hebrew: "he", hébreu: "he", hebreu: "he",
  hindi: "hi",
  turkish: "tr", turc: "tr",
  ukrainian: "uk", ukrainien: "uk",
  vietnamese: "vi", vietnamien: "vi",
  thai: "th", thaï: "th",
  indonesian: "id", indonésien: "id", indonesien: "id",
  malay: "ms", malais: "ms",
  hungarian: "hu", hongrois: "hu",
  romanian: "ro", roumain: "ro",
  bulgarian: "bg", bulgare: "bg",
  catalan: "ca",
  croatian: "hr", croate: "hr",
  slovak: "sk", slovaque: "sk",
  slovenian: "sl", slovène: "sl", slovene: "sl",
  serbian: "sr", serbe: "sr",
  persian: "fa", persan: "fa", farsi: "fa",
  swahili: "sw",
  tagalog: "tl", filipino: "tl",
  bengali: "bn",
  urdu: "ur", ourdou: "ur",
} as const;

const CC_BY_LANG = new Map(Object.entries(LANG_TO_CC));
const LANG_BY_NAME = new Map(Object.entries(NAMES));

export function countryCodeForLanguage(input: string): string {
  const raw = (input || "").trim().toLowerCase().replace(/_/g, "-");
  if (!raw || raw === "und" || raw === "autre" || raw === "other") return "";
  const direct = CC_BY_LANG.get(raw);
  if (direct) return direct;
  const fromName = LANG_BY_NAME.get(raw);
  if (fromName) {
    const named = CC_BY_LANG.get(fromName);
    if (named) return named;
  }
  const parts = raw.split("-").filter(Boolean);
  if (parts.length >= 2) {
    const region = parts[parts.length - 1];
    if (/^[a-z]{2}$/.test(region) && region !== parts[0]) return region;
  }
  const first = parts[0] || "";
  const fromFirstName = LANG_BY_NAME.get(first);
  if (CC_BY_LANG.has(first)) return CC_BY_LANG.get(first) || "";
  if (fromFirstName) return CC_BY_LANG.get(fromFirstName) || "";
  return "";
}

export function flagUrl(input: string, height = 20): string {
  const cc = countryCodeForLanguage(input);
  if (!cc || cc === "un" || cc === "eu") return "";
  return `https://flagcdn.com/h${height}/${cc}.png`;
}
