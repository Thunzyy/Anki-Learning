import { useEffect, useState } from "react";
import { api, type Card, type Deck } from "../api";
import { useLocale } from "../i18n/LocaleContext";
import { FlipCard } from "./FlipCard";

export function CardEditor({
  deck,
  card,
  onClose,
  onSave,
  onDelete,
  onEnrich,
}: {
  deck: Deck;
  card: Card;
  onClose: () => void;
  onSave: (fields: Partial<Card>) => Promise<void>;
  onDelete: () => Promise<void>;
  onEnrich?: () => Promise<void>;
}) {
  const { t } = useLocale();
  const [form, setForm] = useState(card);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [imageQuery, setImageQuery] = useState("");
  const [imageResults, setImageResults] = useState<Awaited<ReturnType<typeof api.imageAssets>>>([]);
  const [verified, setVerified] = useState(false);

  useEffect(() => {
    setForm(card);
    setError("");
    setVerified(false);
    setImageResults([]);
  }, [card]);

  function set<K extends keyof Card>(key: K, value: Card[K]) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  async function save() {
    setBusy(true);
    setError("");
    try {
      await onSave({
        surface: form.surface,
        lemma: form.surface.trim() || form.lemma,
        french: form.french,
        ipa: form.ipa,
        phrase: form.phrase,
        phrase_fr: form.phrase_fr,
        category: form.category,
        image_url: form.image_url,
        image_source: form.image_source,
        image_concept: form.image_concept || "",
        image_scene: form.image_scene || "",
        image_context_verified: verified,
        audio_url: form.audio_url,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("saveFail"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(event) => event.stopPropagation()}>
        <div className="tag">{t("modify")}</div>
        <h2>{form.surface || t("card")}</h2>
        <FlipCard card={form} targetLabel={deck.target_label || t("translation")} features={deck.features} />
        <label>
          {t("word")}
          <input value={form.surface} onChange={(e) => set("surface", e.target.value)} />
        </label>
        <label>
          {t("translation")} ({deck.target_label || t("target")})
          <input value={form.french} onChange={(e) => set("french", e.target.value)} />
        </label>
        <label>
          {t("category")}
          <input value={form.category} onChange={(e) => set("category", e.target.value)} />
        </label>
        {deck.features.phonetic && (
          <label>
            {t("phonetic")}
            <input value={form.ipa} onChange={(e) => set("ipa", e.target.value)} />
          </label>
        )}
        {deck.features.phrase && (
          <>
            <label>
              {t("examplePhrase")}
              <textarea value={form.phrase} onChange={(e) => set("phrase", e.target.value)} />
            </label>
            <label>
              {t("phraseTranslation")}
              <textarea value={form.phrase_fr} onChange={(e) => set("phrase_fr", e.target.value)} />
            </label>
          </>
        )}
        {deck.features.image && (
          <>
          <label>
            {t("imageUrl")}
            <input value={form.image_url} onChange={(e) => {
              setForm(current => ({ ...current, image_url: e.target.value, image_source: "unknown" }));
              setVerified(false);
            }} />
          </label>
          <label>{t("imageSource")}
            <select value={form.image_source || "unknown"} onChange={e => set("image_source", e.target.value)}>
              {["unknown", "generated", "grok", "gemini", "openai", "wikimedia", "unsplash", "pexels", "manual"].map(source =>
                <option key={source} value={source}>{source === "generated" ? t("imageLegacy") : source}</option>)}
            </select>
          </label>
          <label>{t("imageConcept")}
            <input value={form.image_concept || ""} onChange={e => set("image_concept", e.target.value)} />
          </label>
          <label>{t("imageScene")}
            <input value={form.image_scene || ""} onChange={e => set("image_scene", e.target.value)} />
          </label>
          <label className="image-generate">
            <input type="checkbox" checked={verified} onChange={e => setVerified(e.target.checked)} />
            {t("imageValidateContext")}
          </label>
          <details><summary>{t("imageLibrary")}</summary>
            <label>{t("imageSearch")}
              <input value={imageQuery} onChange={e => setImageQuery(e.target.value)} />
            </label>
            <button className="btn ghost" type="button" disabled={busy} onClick={async () => {
              setBusy(true);
              try { setImageResults(await api.imageAssets(imageQuery)); }
              catch (err) { setError(String(err)); }
              finally { setBusy(false); }
            }}>{t("imageSearch")}</button>
            <div className="image-plan">{imageResults.map(asset => <article key={asset.id}>
              <img src={asset.url} alt={asset.metadata.meaning || asset.metadata.word || asset.source} loading="lazy" />
              <span>{asset.metadata.meaning || asset.metadata.word} · {asset.source}</span>
              <button className="btn ghost" type="button" onClick={() => {
                setForm(current => ({ ...current, image_url: asset.url, image_source: asset.source }));
                setVerified(true);
              }}>{t("imageUse")}</button>
            </article>)}</div>
          </details>
          {form.image_provenance && <details><summary>{t("imageSource")}</summary>
            <pre className="image-provenance">{JSON.stringify(form.image_provenance, null, 2)}</pre>
          </details>}
          {!!form.image_history?.length && <details><summary>{t("imageHistory")} ({form.image_history.length})</summary>
            {form.image_history.map((event, i) => <p key={i} className="meta">
              {event.at} · {event.before.source} → {event.after.source} · {event.reason}
            </p>)}
          </details>}
          </>
        )}
        {error && <div className="errors">{error}</div>}
        <div className="actions">
          <button className="btn" disabled={busy} type="button" onClick={save}>
            {busy ? "…" : t("save")}
          </button>
          {onEnrich && (
            <button
              className="btn brass"
              type="button"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setError("");
                try {
                  await onEnrich();
                } catch (err) {
                  setError(err instanceof Error ? err.message : t("aiFail"));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {t("completeAi")}
            </button>
          )}
          <button className="btn ghost" type="button" onClick={onClose}>{t("close")}</button>
          <button
            className="btn ghost"
            type="button"
            onClick={() => {
              if (window.confirm(t("confirmDeleteThis"))) onDelete();
            }}
          >
            {t("delete")}
          </button>
        </div>
      </aside>
    </div>
  );
}
