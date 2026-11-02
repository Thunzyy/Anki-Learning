/** Stage order mirrored from the API protocol (studio/api/app/protocol.py). */
export const STAGES = [
  "ingest",
  "normalize",
  "translate",
  "phonetic",
  "sentences",
  "images",
  "audio",
  "qa",
  "export",
] as const;

export const STAGE_LABEL_KEYS = {
  ingest: "words",
  normalize: "normalize",
  translate: "translation",
  phonetic: "phonetic",
  sentences: "phrase",
  images: "image",
  audio: "audio",
  qa: "qa",
  export: "export",
} as const satisfies Record<(typeof STAGES)[number], string>;
