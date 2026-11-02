import { useEffect, useState, type FormEvent } from "react";
import { api, type Meta, type OAuthStatus, type Settings } from "../api";
import { Flag } from "../components/Flag";
import { GithubAssets, type ReleaseAsset } from "../components/GithubAssets";
import { useLocale } from "../i18n/LocaleContext";
import type { MsgKey } from "../i18n/translations";

const IMAGE_PROMPT_DEFAULT =
  "Create a square pedagogical photo that makes the target word {lemma} " +
  "(French meaning: {french}; category: {category}) immediately unmistakable. " +
  "Use exactly this example situation: {phrase} " +
  "French version of the same situation: {phrase_fr} " +
  "The target concept {sense} must be the central subject or action. " +
  "Do not focus on incidental objects, people, or scenery from the sentence. " +
  "For a verb, show the action in progress, not only its result. " +
  "For an object, food, place, or tool, show the target itself filling most of the frame. " +
  "Avoid visible faces unless the target explicitly requires a person, emotion, or facial action. " +
  "Use one clear, realistic scene a language learner can recognize immediately. " +
  "No text, no letters, no watermark, no brand, no logos.";

const EMPTY: Settings = {
  anthropic_api_key: "",
  openai_api_key: "",
  xai_api_key: "",
  unsplash_access_key: "",
  pexels_api_key: "",
  default_image_bank: "grok",
  default_ai_provider: "grok",
  default_target_language: "fr",
  default_target_label: "Français",
  github_repo: "Thunzyy/Anki-Learning",
  github_release: "decks",
  anki_connect_url: "http://127.0.0.1:8765",
  image_prompt: IMAGE_PROMPT_DEFAULT,
  image_concurrency: 4,
};

function previewImagePrompt(template: string) {
  const lemma = "mix";
  const french = "mélanger";
  const sense = `${lemma} (${french})`;
  const phrase = "Mix the flour with the eggs carefully.";
  return (template || IMAGE_PROMPT_DEFAULT)
    .replaceAll("{lemma}", lemma)
    .replaceAll("{french}", french)
    .replaceAll("{sense}", sense)
    .replaceAll("{phrase}", phrase)
    .replaceAll("{phrase_fr}", "Mélangez la farine avec les œufs soigneusement.")
    .replaceAll("{category}", "verbe");
}

function canonicalAiProvider(id: string): string {
  switch (id) {
    case "anthropic":
      return "claude";
    case "openai":
      return "codex";
    case "xai":
      return "grok";
    default:
      return id;
  }
}

type AiChoice =
  | { id: string; label: string }
  | { id: string; labelKey: MsgKey };

const AI_CHOICES: AiChoice[] = [
  { id: "grok", label: "Grok Build (xAI)" },
  { id: "claude", label: "Claude (Anthropic)" },
  { id: "codex", label: "Codex (ChatGPT)" },
  { id: "local", labelKey: "localFallback" },
];

const IMAGE_CHOICES = [
  { id: "grok", label: "Grok Imagine (IA)" },
  { id: "codex", label: "Codex (OpenAI)" },
  { id: "wikimedia", label: "Wikimedia / Wikipédia" },
  { id: "unsplash", label: "Unsplash" },
  { id: "pexels", label: "Pexels" },
];

export function SettingsPage() {
  const { t } = useLocale();
  const [form, setForm] = useState<Settings>(EMPTY);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [oauth, setOauth] = useState<OAuthStatus[]>([]);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [hint, setHint] = useState("");
  const [busy, setBusy] = useState("");
  const [assets, setAssets] = useState<ReleaseAsset[]>([]);
  const [releaseUrl, setReleaseUrl] = useState("");

  async function refreshAuth() {
    const data = await api.authStatus();
    setOauth(data.providers);
  }

  async function refreshAssets() {
    try {
      const listing = await api.listPublishAssets();
      setAssets(listing.assets);
      setReleaseUrl(listing.url);
    } catch {
      setAssets([]);
    }
  }

  useEffect(() => {
    api.meta().then(setMeta).catch(() => undefined);
    api.settings().then((data) => {
      setForm({
        ...data,
        default_ai_provider: canonicalAiProvider(data.default_ai_provider),
        default_target_language: data.default_target_language || "fr",
        default_target_label: data.default_target_label || "Français",
      });
    }).catch((err: Error) => setError(err.message));
    refreshAuth().catch((err: Error) => setError(err.message));
    refreshAssets().catch(() => undefined);
  }, []);

  function set<K extends keyof Settings>(key: K, value: Settings[K]) {
    setForm((current) => ({ ...current, [key]: value }));
    setSaved(false);
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      await api.saveSettings(form);
      setSaved(true);
      await refreshAssets();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("fail"));
    }
  }

  async function deleteAsset(name: string) {
    setBusy("github");
    setError("");
    try {
      await api.deletePublishAsset(name);
      await refreshAssets();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("fail"));
    } finally {
      setBusy("");
    }
  }

  async function login(provider: string) {
    setBusy(provider);
    setError("");
    try {
      const result = await api.authLogin(provider);
      setHint(result.hint);
      window.setTimeout(() => {
        refreshAuth().catch(() => undefined);
      }, 2500);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("loginFail"));
    } finally {
      setBusy("");
    }
  }

  return (
    <section>
      <div className="tag">{t("settingsTag")}</div>
      <h1 className="display">{t("settingsTitle")}</h1>
      <p className="lede">{t("settingsLede")}</p>
      <form className="form" onSubmit={submit} style={{ marginBottom: 28 }}>
        <div className="tag">{t("defaults")}</div>
        <div className="row">
          <label>
            {t("defaultAi")}
            <select value={form.default_ai_provider} onChange={(e) => set("default_ai_provider", e.target.value)}>
              {AI_CHOICES.map((item) => (
                <option key={item.id} value={item.id}>
                  {"labelKey" in item ? t(item.labelKey) : item.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            {t("defaultImages")}
            <select value={form.default_image_bank === "openai" ? "codex" : form.default_image_bank} onChange={(e) => set("default_image_bank", e.target.value)}>
              {IMAGE_CHOICES.map((item) => (
                <option key={item.id} value={item.id}>{item.label}</option>
              ))}
            </select>
          </label>
        </div>
        <label>
          {t("imageWorkers")}
          <input
            type="number"
            min={1}
            max={64}
            step={1}
            required
            value={form.image_concurrency}
            onChange={(e) => set("image_concurrency", Number(e.target.value))}
          />
        </label>
        <div className="meta">{t("imageWorkersHelp")}</div>
        <label>
          {t("defaultTarget")}
          <div className="lang-field">
            <Flag code={form.default_target_language || form.default_target_label} />
            <input
              list="target-lang-list"
              value={form.default_target_label}
              onChange={(e) => {
                const label = e.target.value;
                const hit = (meta?.languages || []).find((item) => item.label === label || item.id === label.toLowerCase());
                setForm((current) => ({
                  ...current,
                  default_target_label: label,
                  default_target_language: hit?.id || label,
                }));
                setSaved(false);
              }}
              placeholder={t("translateToPh")}
            />
          </div>
          <datalist id="target-lang-list">
            {(meta?.languages || []).map((item) => (
              <option key={item.id} value={item.label} />
            ))}
          </datalist>
        </label>
        <div className="row">
          <label>
            {t("githubRepo")}
            <input value={form.github_repo || ""} onChange={(e) => set("github_repo", e.target.value)} />
          </label>
          <label>
            {t("githubRelease")}
            <input value={form.github_release || ""} onChange={(e) => set("github_release", e.target.value)} />
          </label>
        </div>
        <label>
          {t("ankiConnect")}
          <input value={form.anki_connect_url || ""} onChange={(e) => set("anki_connect_url", e.target.value)} />
        </label>
        <label>
          {t("imagePrompt")}
          <textarea
            className="prompt-editor"
            value={form.image_prompt || ""}
            onChange={(e) => set("image_prompt", e.target.value)}
            spellCheck={false}
          />
        </label>
        <div className="meta">{t("imagePromptHelp")}</div>
        <div className="prompt-preview">
          <div className="tag">{t("imagePromptPreview")}</div>
          <p>{previewImagePrompt(form.image_prompt || "")}</p>
        </div>
        <button className="btn ghost" type="button" onClick={() => set("image_prompt", IMAGE_PROMPT_DEFAULT)}>
          {t("imagePromptReset")}
        </button>
        {saved && <div className="banner">{t("saved")}</div>}
        <button className="btn" type="submit">{t("saveDefaults")}</button>
      </form>
      <div className="gh-manage">
        <div className="section-title">{t("githubManage")}</div>
        {releaseUrl && (
          <p className="meta">
            <a href={releaseUrl} target="_blank" rel="noreferrer">{releaseUrl}</a>
          </p>
        )}
        <GithubAssets
          assets={assets}
          busy={busy === "github"}
          emptyLabel={t("githubNoAssets")}
          folderLabel={t("githubKindFolder")}
          deckLabel={t("githubKindDeck")}
          deleteLabel={t("githubDelete")}
          confirmTemplate={t("confirmDeleteAsset")}
          onDelete={deleteAsset}
        />
      </div>
      <div className="oauth-grid">
        {oauth.map((item) => (
          <article key={item.provider} className={`oauth-card ${item.connected ? "ok" : "off"}`}>
            <div className="meta">{item.provider}</div>
            <h3>{item.label}</h3>
            <p>
              {item.connected
                ? t("connected", { account: item.account, source: item.source })
                : t("notConnected")}
            </p>
            <div className="actions">
              <button className="btn ghost" type="button" onClick={() => refreshAuth()}>
                {t("reread")}
              </button>
              <button className="btn" type="button" disabled={busy === item.provider} onClick={() => login(item.provider)}>
                {item.connected ? t("reconnect") : t("openLogin")}
              </button>
            </div>
            <div className="meta">{item.login_command}</div>
          </article>
        ))}
      </div>
      {hint && <div className="banner">{hint}</div>}
      <form className="form" onSubmit={submit} style={{ marginTop: 28 }}>
        <div className="tag">{t("apiKeys")}</div>
        <label>Anthropic <input type="password" value={form.anthropic_api_key} onChange={(e) => set("anthropic_api_key", e.target.value)} /></label>
        <label>xAI Grok <input type="password" value={form.xai_api_key} onChange={(e) => set("xai_api_key", e.target.value)} /></label>
        <label>OpenAI <input type="password" value={form.openai_api_key} onChange={(e) => set("openai_api_key", e.target.value)} /></label>
        <label>Unsplash <input type="password" value={form.unsplash_access_key} onChange={(e) => set("unsplash_access_key", e.target.value)} /></label>
        <label>Pexels <input type="password" value={form.pexels_api_key} onChange={(e) => set("pexels_api_key", e.target.value)} /></label>
        {error && <div className="errors">{error}</div>}
        {saved && <div className="banner">{t("saved")}</div>}
        <button className="btn" type="submit">{t("saveKeys")}</button>
      </form>
    </section>
  );
}
