export type StageStatus = "pending" | "running" | "passed" | "failed" | "blocked" | "skipped";

export type DeckFeatures = {
  phrase: boolean;
  phonetic: boolean;
  audio: boolean;
  image: boolean;
};

export type Card = {
  id: string;
  lemma: string;
  surface: string;
  language: string;
  french: string;
  category: string;
  ipa: string;
  phrase: string;
  phrase_fr: string;
  image_url: string;
  image_source: string;
  image_concept?: string;
  image_scene?: string;
  image_asset_id?: string;
  image_provenance?: { source?: string; origin?: string; model?: string; prompt?: string; sha256?: string; context_status?: string };
  image_context_verified?: boolean;
  image_history?: { at: string; reason: string; before: { url: string; source: string }; after: { url: string; source: string } }[];
  audio_url: string;
  skip_image: boolean;
  flags: string[];
  errors: string[];
};

export type Deck = {
  id: string;
  name: string;
  language: string;
  language_label: string;
  target_language: string;
  target_label: string;
  image_bank: string;
  ai_provider: string;
  source: string;
  kind?: "vocab" | "grammar";
  features: DeckFeatures;
  cards: Card[];
  card_count?: number;
  stages: Record<string, StageStatus>;
  stage_errors?: Record<string, string[]>;
  created_at: string;
  updated_at: string;
};

export type ImagePlan = {
  revision: string;
  total: number; matched: number; unmatched: number; changed: number; backup: string;
  errors: { id: string; message: string }[];
  items: { id: string; word: string; action: string;
    before: { url: string; source: string };
    replacement: { url: string; source: string } | null }[];
};

export type Meta = {
  stages: string[];
  image_banks: { id: string; label: string; needs_key: boolean }[];
  ai_providers: { id: string; label: string; needs_key: boolean }[];
  languages: { id: string; label: string }[];
  strategies?: {
    id: string;
    language: string;
    language_label: string;
    label: string;
    available: number;
    sizes: number[];
    phrase: boolean;
    audio: boolean;
    kind?: "frequency" | "grammar";
    cefr?: string;
  }[];
  keys: Record<string, boolean>;
  defaults?: {
    ai_provider: string;
    image_bank: string;
    target_language: string;
    target_label: string;
    image_concurrency: number;
  };
  image_generation?: { ok: boolean; reason: string; provider: string };
};

export type Settings = {
  anthropic_api_key: string;
  openai_api_key: string;
  xai_api_key: string;
  unsplash_access_key: string;
  pexels_api_key: string;
  default_image_bank: string;
  default_ai_provider: string;
  default_target_language: string;
  default_target_label: string;
  github_repo?: string;
  github_release?: string;
  anki_connect_url?: string;
  image_prompt?: string;
  image_concurrency: number;
};

export type OAuthStatus = {
  provider: string;
  label: string;
  connected: boolean;
  source: string;
  account: string;
  expires_at: string;
  path: string;
  login_command: string;
};

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers });
  const raw = await response.text();
  if (!response.ok) {
    let message = response.statusText || `HTTP ${response.status}`;
    if (raw) {
      try {
        const body: unknown = JSON.parse(raw);
        if (body instanceof Object && !Array.isArray(body) && "detail" in body) {
          const detail = body.detail;
          if (detail == null) message = raw;
          else if (Array.isArray(detail) || detail instanceof Object) message = JSON.stringify(detail);
          else message = String(detail);
        } else {
          message = raw;
        }
      } catch {
        message = raw;
      }
    }
    throw new ApiError(message, response.status);
  }
  if (!raw) {
    throw new Error("réponse vide");
  }
  const payload: unknown = JSON.parse(raw);
  // SAFETY: each api.* method names the JSON contract this Learning endpoint returns.
  return payload as T;
}

export const api = {
  imageAssets: (query: string) => request<{ id: string; url: string; source: string; metadata: { word?: string; meaning?: string } }[]>(`/api/image-library/assets?q=${encodeURIComponent(query)}&limit=12`),
  replaceImages: (id: string, payload: { source: string; destination: string; generate_missing: boolean; preview: boolean; ids?: string[]; limit?: number; expected_revision?: string }) =>
    request<ImagePlan>(`/api/decks/${id}/images/replace`, { method: "POST", body: JSON.stringify(payload) }),
  meta: () => request<Meta>("/api/meta"),
  settings: () => request<Settings>("/api/settings"),
  saveSettings: (payload: Settings) =>
    request<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(payload) }),
  decks: () => request<Deck[]>("/api/decks"),
  deck: (id: string) => request<Deck>(`/api/decks/${id}`),
  createDeck: (payload: {
    name: string;
    language: string;
    language_label: string;
    target_language: string;
    target_label: string;
    words: string[];
    source_id?: string;
    limit?: number | null;
    features: DeckFeatures;
  }) => request<Deck>("/api/decks", { method: "POST", body: JSON.stringify(payload) }),
  deleteDeck: (id: string) => request<{ ok: boolean }>(`/api/decks/${id}`, { method: "DELETE" }),
  renameDeck: (id: string, name: string) =>
    request<Deck>(`/api/decks/${id}`, { method: "PATCH", body: JSON.stringify({ name }) }),
  patchDeck: (id: string, payload: { name?: string; features?: DeckFeatures }) =>
    request<Deck>(`/api/decks/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  addCards: (id: string, words: string[]) =>
    request<Deck>(`/api/decks/${id}/cards`, { method: "POST", body: JSON.stringify({ words, enrich: true }) }),
  enrichCards: (
    id: string,
    ids: string[] = [],
    limit = 20,
    features?: DeckFeatures,
    concurrency = 4,
    retry = false,
    generateImages = false,
  ) =>
    request<Deck>(`/api/decks/${id}/enrich`, {
      method: "POST",
      body: JSON.stringify({
        ids,
        limit,
        features,
        concurrency,
        retry,
        generate_images: generateImages,
      }),
    }),
  patchCard: (deckId: string, cardId: string, fields: Partial<Card>) =>
    request<Deck>(`/api/decks/${deckId}/cards/${cardId}`, { method: "PATCH", body: JSON.stringify(fields) }),
  deleteCard: (deckId: string, cardId: string) =>
    request<Deck>(`/api/decks/${deckId}/cards/${cardId}`, { method: "DELETE" }),
  bulkDeleteCards: (deckId: string, ids: string[]) =>
    request<Deck>(`/api/decks/${deckId}/cards/bulk-delete`, { method: "POST", body: JSON.stringify({ ids }) }),
  runStage: (id: string, stage: string) =>
    request<Deck>(`/api/decks/${id}/run/${stage}`, { method: "POST" }),
  exportCsv: (id: string) =>
    request<{ ok: boolean; filename: string }>(`/api/decks/${id}/export/csv`, { method: "POST" }),
  exportApkg: (id: string) =>
    request<{ ok: boolean; filename: string }>(`/api/decks/${id}/export/apkg`, { method: "POST" }),
  publishDeck: (id: string) =>
    request<{ ok: boolean; tag: string; asset: string; url: string; download: string; warnings?: string[] }>(
      `/api/decks/${id}/publish`,
      { method: "POST" },
    ),
  publishStatus: (id: string) =>
    request<{ published: boolean; url: string; download: string; asset: string; tag: string }>(
      `/api/decks/${id}/publish`,
    ),
  languagePublishStatus: (langId: string) =>
    request<{
      published: boolean;
      url: string;
      download: string;
      asset: string;
      tag: string;
      kind?: string;
      assets?: { name: string; kind: string; download: string; size: number }[];
    }>(`/api/languages/${encodeURIComponent(langId)}/publish`),
  publishLanguage: (langId: string, mode: "folder" | "separate") =>
    request<{
      ok: boolean;
      tag: string;
      asset: string;
      url: string;
      download: string;
      files?: number;
      published?: number;
      skipped?: string[];
      folder?: string;
      mode?: string;
    }>(`/api/languages/${encodeURIComponent(langId)}/publish?mode=${mode}`, { method: "POST" }),
  unpublishLanguage: (langId: string) =>
    request<{ ok: boolean; deleted: string[]; tag: string }>(
      `/api/languages/${encodeURIComponent(langId)}/publish`,
      { method: "DELETE" },
    ),
  unpublishDeck: (id: string) =>
    request<{ ok: boolean; deleted: string }>(`/api/decks/${id}/publish`, { method: "DELETE" }),
  listPublishAssets: () =>
    request<{
      ok: boolean;
      repo: string;
      tag: string;
      url: string;
      assets: { name: string; kind: string; download: string; size: number }[];
    }>("/api/publish"),
  deletePublishAsset: (name: string) =>
    request<{ ok: boolean; deleted: string }>(
      `/api/publish/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),
  sendToAnki: (id: string) =>
    request<{ ok: boolean; deck: string; url: string; cards: number }>(`/api/decks/${id}/anki`, { method: "POST" }),
  ankiStatus: () => request<{ ok: boolean; url: string; version?: number; error?: string }>("/api/anki/status"),
  libraryImageStats: () => request<{ images: number; illustrated_cards: number }>("/api/library/images/stats"),
  authStatus: () => request<{ providers: OAuthStatus[] }>("/api/auth/status"),
  authLogin: (provider: string) =>
    request<{ started: boolean; provider: string; command: string; hint: string }>(
      `/api/auth/${provider}/login`,
      { method: "POST" },
    ),
};
