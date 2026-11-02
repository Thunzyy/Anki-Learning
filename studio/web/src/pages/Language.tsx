import { useEffect, useMemo, useState } from "react";
import { ApiError, api, type Deck, type Meta, type Settings } from "../api";
import { DeckGrid } from "../components/DeckGrid";
import { Flag } from "../components/Flag";
import { GithubAssets } from "../components/GithubAssets";
import { GithubStatus, type ReleaseStatus } from "../components/GithubMark";
import { useLocale } from "../i18n/LocaleContext";

const CEFR = ["a1", "a2", "b1", "b2", "c1"] as const;

function isGrammar(deck: Deck) {
  return deck.kind === "grammar" || (deck.source || "").includes("grammar-");
}

export function LanguagePage({ langId }: { langId: string }) {
  const { t } = useLocale();
  const [decks, setDecks] = useState<Deck[] | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [progress, setProgress] = useState("");
  const [sentNote, setSentNote] = useState("");
  const [release, setRelease] = useState<ReleaseStatus | null>(null);

  useEffect(() => {
    api.decks().then(setDecks).catch((err: Error) => {
      setDecks([]);
      setError(err.message);
    });
    api.meta().then(setMeta).catch((err: Error) => setError(err.message));
    api.settings().then(setSettings).catch((err: Error) => setError(err.message));
    api.languagePublishStatus(langId).then(setRelease).catch(() => setRelease({
      published: false,
      url: "",
      download: "",
      asset: "",
      tag: "",
      kind: "",
      assets: [],
    }));
  }, [langId]);

  const language = useMemo(
    () => (meta?.languages || []).find((item) => item.id === langId),
    [meta, langId],
  );

  const loaded = decks !== null;
  const all = decks || [];
  const mine = all.filter((deck) => deck.language === langId);
  const vocab = mine.filter((deck) => !isGrammar(deck));
  const grammar = mine.filter(isGrammar);
  const totalCards = mine.reduce((sum, deck) => sum + (deck.card_count ?? deck.cards.length), 0);

  async function createGrammar(level: string) {
    if (!language || busy) return;
    setBusy(level);
    setError("");
    try {
      const deck = await api.createDeck({
        name: t("grammarDeckName", { level: level.toUpperCase(), language: language.label }),
        language: language.id,
        language_label: language.label,
        target_language: settings?.default_target_language || "fr",
        target_label: settings?.default_target_label || "Français",
        words: [],
        source_id: `grammar-${language.id}-${level}`,
        features: { phrase: true, phonetic: false, audio: false, image: false },
      });
      window.location.hash = `#/deck/${deck.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : t("createFail"));
    } finally {
      setBusy("");
    }
  }

  async function sendAllToAnki() {
    if (busy || !mine.length) return;
    setBusy("anki");
    setError("");
    setSentNote("");
    setProgress(t("langSendingAll", { done: 1, total: mine.length }));
    try {
      const status = await api.ankiStatus();
      if (!status.ok) {
        setError(t("ankiClosed", { url: status.url }));
        return;
      }
      let sent = 0;
      let cards = 0;
      let folder = "";
      const skipped: string[] = [];
      for (const [index, deck] of mine.entries()) {
        setProgress(t("langSendingAll", { done: index + 1, total: mine.length }));
        try {
          const result = await api.sendToAnki(deck.id);
          sent += 1;
          cards += result.cards ?? 0;
          folder = folder || result.deck.split("::")[0];
        } catch (err) {
          // 409 means this deck is not QA-green yet; anything else is a real fault.
          if (err instanceof ApiError && err.status === 409) {
            skipped.push(deck.name);
            continue;
          }
          throw err;
        }
      }
      const note = [
        t("langSentAll", { n: sent, cards, folder: folder || language?.label || langId }),
        skipped.length ? t("langSentSkipped", { n: skipped.length, decks: skipped.join(", ") }) : "",
      ]
        .filter(Boolean)
        .join(" · ");
      setSentNote(note);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("ankiFail"));
    } finally {
      setBusy("");
      setProgress("");
    }
  }

  async function refreshRelease() {
    try {
      setRelease(await api.languagePublishStatus(langId));
    } catch {
      setRelease({
        published: false,
        url: "",
        download: "",
        asset: "",
        tag: "",
        kind: "",
        assets: [],
      });
    }
  }

  async function publishLanguage(mode: "folder" | "separate") {
    if (busy || !mine.length) return;
    setBusy(mode === "folder" ? "publish" : "publish-decks");
    setError("");
    setSentNote("");
    try {
      const result = await api.publishLanguage(langId, mode);
      const skipped = result.skipped || [];
      await refreshRelease();
      setSentNote(
        [
          t(mode === "folder" ? "langPublishedAll" : "langPublishedDecks", {
            n: result.published ?? result.files ?? 0,
            asset: result.asset,
            folder: result.folder || language?.label || langId,
          }),
          skipped.length ? t("langSentSkipped", { n: skipped.length, decks: skipped.join(", ") }) : "",
        ]
          .filter(Boolean)
          .join(" · "),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : t("exportBlocked"));
    } finally {
      setBusy("");
    }
  }

  async function unpublishLanguage() {
    if (busy) return;
    setBusy("unpublish");
    setError("");
    setSentNote("");
    try {
      await api.unpublishLanguage(langId);
      await refreshRelease();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("exportBlocked"));
    } finally {
      setBusy("");
    }
  }

  async function deleteAsset(name: string) {
    if (busy) return;
    setBusy("unpublish");
    setError("");
    try {
      await api.deletePublishAsset(name);
      await refreshRelease();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("exportBlocked"));
    } finally {
      setBusy("");
    }
  }

  if (meta && !language) {
    return (
      <section>
        <h1 className="display">{t("langUnknown")}</h1>
        <p className="lede">{t("langUnknownHint")}</p>
        <div className="actions" style={{ marginTop: 20 }}>
          <a className="btn" href="#/">{t("navDecks")}</a>
        </div>
      </section>
    );
  }

  const label = language?.label || langId;

  return (
    <section>
      <div className="lang-head">
        <div>
          <h1>
            <Flag code={langId} title={label} />
            {label}
          </h1>
          <p className="meta lang-line">
            {loaded
              ? t("langCounts", { vocab: vocab.length, grammar: grammar.length, cards: totalCards })
              : "\u00a0"}
            {release && (
              <GithubStatus
                release={release}
                onLabel={t("onGithub")}
                offLabel={t("notOnGithub")}
              />
            )}
          </p>
        </div>
        <div className="actions">
          <a className="btn" href={`#/new?lang=${encodeURIComponent(langId)}`}>{t("langNewVocab")}</a>
          {loaded && mine.length > 0 && (
            <>
              <button
                type="button"
                className="btn brass"
                disabled={Boolean(busy)}
                onClick={sendAllToAnki}
              >
                {busy === "anki" ? progress : t("langSendAll")}
              </button>
              <button
                type="button"
                className="btn ghost"
                disabled={Boolean(busy)}
                onClick={() => publishLanguage("folder")}
              >
                {busy === "publish" ? t("langPublishing") : t("langPublishAll")}
              </button>
              <button
                type="button"
                className="btn ghost"
                disabled={Boolean(busy)}
                onClick={() => publishLanguage("separate")}
              >
                {busy === "publish-decks" ? t("langPublishingDecks") : t("langPublishDecks")}
              </button>
              {release?.published && (
                <button
                  type="button"
                  className="btn ghost danger"
                  disabled={Boolean(busy)}
                  onClick={unpublishLanguage}
                >
                  {busy === "unpublish" ? t("langUnpublishing") : t("langUnpublish")}
                </button>
              )}
            </>
          )}
          {CEFR.map((level) => (
            <button
              key={level}
              type="button"
              className="btn ghost"
              disabled={Boolean(busy)}
              onClick={() => createGrammar(level)}
            >
              {busy === level ? t("langCreating") : level.toUpperCase()}
            </button>
          ))}
        </div>
      </div>
      {sentNote && <div className="banner">{sentNote}</div>}
      {error && <div className="errors">{error}</div>}
      {release && (release.assets?.length || 0) > 0 && (
        <div className="gh-manage">
          <div className="section-title">{t("githubAssets")}</div>
          <GithubAssets
            assets={release.assets || []}
            busy={Boolean(busy)}
            emptyLabel={t("githubNoAssets")}
            folderLabel={t("githubKindFolder")}
            deckLabel={t("githubKindDeck")}
            deleteLabel={t("githubDelete")}
            confirmTemplate={t("confirmDeleteAsset")}
            onDelete={deleteAsset}
          />
        </div>
      )}
      {!loaded && (
        <div className="skeleton-grid">
          {[0, 1, 2, 3].map((slot) => (
            <div key={slot} className="skeleton" />
          ))}
        </div>
      )}
      {loaded && (
        <>
          <div className="section-title">{t("langVocab")}</div>
          {vocab.length ? <DeckGrid decks={vocab} /> : <p className="lede">{t("langEmptyVocab")}</p>}
          <div className="section-title">{t("langGrammar")}</div>
          {grammar.length ? <DeckGrid decks={grammar} /> : <p className="lede">{t("langEmptyGrammar")}</p>}
        </>
      )}
    </section>
  );
}
