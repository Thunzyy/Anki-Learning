import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, type Card, type Deck, type DeckFeatures } from "../api";
import { CardEditor } from "../components/CardEditor";
import { ImageTools } from "../components/ImageTools";
import { FeatureIcons } from "../components/FeatureIcons";
import { Flag } from "../components/Flag";
import { FlipCard } from "../components/FlipCard";
import { GithubStatus, type ReleaseStatus } from "../components/GithubMark";
import { useLocale } from "../i18n/LocaleContext";
import { filterAndRankCards } from "../searchCards";
import { STAGES, STAGE_LABEL_KEYS } from "../stages";

const PREVIEW_PAGE = 36;
const ENRICH_BATCH = 20;
const ENRICH_PARALLEL = 4;

function hasUsableImage(card: Card) {
  const url = (card.image_url || "").trim();
  if (!url) return false;
  if (card.image_asset_id) return true;
  const source = (card.image_source || "").toLowerCase();
  if (source === "wikimedia" || source === "unsplash" || source === "pexels") return false;
  if (/wikimedia\.org|unsplash\.com|pexels\.com/i.test(url)) return false;
  return !/\.svg(\b|\?|$)/i.test(url);
}

function cardNeedsFill(deck: Deck, card: Card) {
  if (!card.french.trim()) return true;
  if (deck.features.phonetic && !card.ipa.trim()) return true;
  if (deck.features.phrase && (!card.phrase.trim() || !card.phrase_fr.trim())) return true;
  if (deck.features.image && !imageDone(card)) return true;
  if (deck.features.audio && !card.audio_url) return true;
  return false;
}

function imageDone(card: Card) {
  return card.skip_image
    || card.category.toLowerCase() === "grammaire"
    || hasUsableImage(card);
}

function retryableError(card: Card) {
  return card.errors.some((err) => !/manquante|obligatoire|audio manquant/i.test(err));
}

function qualityError(card: Card) {
  return card.errors.some((err) => /mot cible|trop courte|trop longue|titre de la règle/i.test(err));
}

function coverage(deck: Deck) {
  const n = deck.cards.length || 1;
  const count = (ok: (card: Card) => boolean) => deck.cards.filter(ok).length;
  return {
    total: deck.cards.length,
    french: count((card) => Boolean(card.french.trim())),
    phonetic: count((card) => Boolean(card.ipa.trim())),
    phrases: count((card) => Boolean(card.phrase.trim() && card.phrase_fr.trim())),
    audio: count((card) => Boolean(card.audio_url.trim())),
    images: count(imageDone),
    errors: count(retryableError),
    leftover: deck.cards.filter((card) => cardNeedsFill(deck, card)).length,
    ratio: n,
  };
}

const FILTERS = ["all", "missing", "errors"] as const;
type Filter = (typeof FILTERS)[number];

function parseFilter(value: string): Filter {
  for (const item of FILTERS) {
    if (item === value) return item;
  }
  return "all";
}

function Tile({
  label,
  value,
  total,
  extra,
}: {
  label: string;
  value: number;
  total?: number;
  extra?: ReactNode;
}) {
  const done = total !== undefined && value >= total;
  return (
    <article className={`dash-tile ${done ? "full" : ""}`}>
      <div className="meta">{label}</div>
      <strong>
        {value}
        {total !== undefined && <span> / {total}</span>}
        {extra}
      </strong>
      {total !== undefined && (
        <div className="dash-meter">
          <i style={{ width: `${total ? Math.round((value / total) * 100) : 0}%` }} />
        </div>
      )}
    </article>
  );
}

export function Workshop({ deckId }: { deckId: string }) {
  const { t } = useLocale();
  const [deck, setDeck] = useState<Deck | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [download, setDownload] = useState("");
  const [publishNote, setPublishNote] = useState("");
  const [release, setRelease] = useState<ReleaseStatus | null>(null);
  const [ankiNote, setAnkiNote] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<Card | null>(null);
  const [addText, setAddText] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [progress, setProgress] = useState("");
  const [shown, setShown] = useState(PREVIEW_PAGE);
  const sentinel = useRef<HTMLDivElement | null>(null);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, [deckId]);

  async function refresh() {
    const next = await api.deck(deckId);
    setDeck(next);
    return next;
  }

  useEffect(() => {
    refresh().catch((err: Error) => setError(err.message));
    api.publishStatus(deckId).then(setRelease).catch(() => setRelease({
      published: false,
      url: "",
      download: "",
      asset: "",
      tag: "",
    }));
  }, [deckId]);

  useEffect(() => {
    if (busy !== "ai") return;
    const timer = window.setInterval(() => {
      api.deck(deckId).then((next) => {
        if (alive.current) setDeck(next);
      }).catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [busy, deckId]);

  const visible = useMemo(() => {
    if (!deck) return [];
    const scoped = deck.cards.filter((card) => {
      if (filter === "missing" && card.french) return false;
      if (filter === "errors" && !retryableError(card)) return false;
      return true;
    });
    return filterAndRankCards(scoped, query);
  }, [deck, query, filter]);

  useEffect(() => {
    setShown(PREVIEW_PAGE);
  }, [deckId, query, filter]);

  useEffect(() => {
    const el = sentinel.current;
    if (!el || shown >= visible.length) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        setShown((count) => Math.min(count + PREVIEW_PAGE, visible.length));
      }
    }, { rootMargin: "640px 0px" });
    observer.observe(el);
    return () => observer.disconnect();
  }, [shown, visible.length]);

  async function run(kind: "all" | string) {
    if (kind === "all" || ["translate", "phonetic", "sentences", "images", "audio"].includes(kind)) {
      await askAi();
      return;
    }
    setBusy(kind);
    setError("");
    try {
      setDeck(await api.runStage(deckId, kind));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("fail"));
    } finally {
      setBusy("");
    }
  }

  function startDownload(href: string) {
    setDownload(href);
    const link = document.createElement("a");
    link.href = href;
    link.download = href.split("/").pop() || "deck";
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  async function exportFile(kind: "csv" | "apkg") {
    setBusy(kind);
    setError("");
    try {
      const result = kind === "csv" ? await api.exportCsv(deckId) : await api.exportApkg(deckId);
      startDownload(`/api/exports/${result.filename}`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("exportBlocked"));
    } finally {
      setBusy("");
    }
  }

  async function publishGithub() {
    setBusy("publish");
    setError("");
    setPublishNote("");
    try {
      const result = await api.publishDeck(deckId);
      setPublishNote(result.url || result.asset);
      setRelease({
        published: true,
        url: result.url || "",
        download: result.download || "",
        asset: result.asset || "",
        tag: result.tag || "",
      });
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("exportBlocked"));
    } finally {
      setBusy("");
    }
  }

  async function unpublishGithub() {
    setBusy("unpublish");
    setError("");
    setPublishNote("");
    try {
      await api.unpublishDeck(deckId);
      setRelease({
        published: false,
        url: release?.url || "",
        download: "",
        asset: "",
        tag: release?.tag || "",
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("exportBlocked"));
    } finally {
      setBusy("");
    }
  }

  async function sendAnki() {
    setBusy("anki");
    setError("");
    setAnkiNote("");
    try {
      const status = await api.ankiStatus();
      if (status.ok) {
        const result = await api.sendToAnki(deckId);
        setAnkiNote(t("ankiImported", { deck: result.deck, n: String(result.cards ?? 0) }));
      } else {
        // Anki is closed: hand over the package rather than pretending it landed.
        const result = await api.exportApkg(deckId);
        startDownload(`/api/exports/${result.filename}`);
        setError(status.error ? t("ankiOffline", { url: status.url }) : t("ankiFail"));
      }
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("ankiFail"));
    } finally {
      setBusy("");
    }
  }

  function warnIfEmpty(next: Deck, ids: string[] = []) {
    const pool = ids.length ? next.cards.filter((card) => ids.includes(card.id)) : next.cards;
    const empty = pool.filter((card) => !card.french.trim());
    if (empty.length) {
      setError(t("missingTranslations", { n: empty.length }));
    }
    return next;
  }

  async function setFeature(key: keyof DeckFeatures, value: boolean) {
    if (!deck) return;
    const features = { ...deck.features, [key]: value };
    setBusy("features");
    setError("");
    try {
      setDeck(await api.patchDeck(deckId, { features }));
    } catch (err) {
      setError(err instanceof Error ? err.message : t("saveFail"));
    } finally {
      setBusy("");
    }
  }

  async function addWords() {
    const words = addText.split(/\n|,/).map((item) => item.trim()).filter(Boolean);
    if (!words.length) return;
    setBusy("add");
    setError("");
    try {
      setDeck(warnIfEmpty(await api.addCards(deckId, words)));
      setAddText("");
    } catch (err) {
      setError(err instanceof Error ? err.message : t("addFail"));
    } finally {
      setBusy("");
    }
  }

  async function askAi(ids: string[] = [], retry = false) {
    setBusy("ai");
    setError("");
    setProgress("");
    try {
      let next = deck;
      if (!next) next = await refresh();
      const leftoverIds = next.cards.filter((card) => cardNeedsFill(next!, card)).map((card) => card.id);
      const errorIds = next.cards.filter(retryableError).map((card) => card.id);
      const pool = ids.length ? ids : [...new Set([...leftoverIds, ...errorIds])];
      const shouldRetry = retry || pool.some((id) => errorIds.includes(id));
      const total = pool.length;
      if (!total) {
        setError(t("nothingToFill"));
        return;
      }
      async function walk(
        start: string[],
        batchSize: number,
        parallel: number,
        generateImages: boolean,
        labelTotal: number,
      ) {
        const tried = new Set<string>();
        let pending = start;
        while (pending.length > 0 && alive.current) {
          const batch = pending.slice(0, batchSize);
          batch.forEach((id) => tried.add(id));
          setProgress(t(generateImages ? "generatingImages" : "aiProgress", {
            done: tried.size,
            total: labelTotal,
          }));
          next = await api.enrichCards(
            deckId,
            batch,
            batch.length,
            next!.features,
            parallel,
            shouldRetry,
            generateImages,
          );
          if (!alive.current) return;
          setDeck(next);
          pending = next.cards
            .filter((card) => {
              if (ids.length && !ids.includes(card.id)) return false;
              if (tried.has(card.id)) return false;
              if (generateImages) return !imageDone(card);
              return cardNeedsFill(next!, card) || retryableError(card);
            })
            .map((card) => card.id);
        }
      }

      await walk(pool, ENRICH_BATCH * ENRICH_PARALLEL, ENRICH_PARALLEL, false, total);
      if (!alive.current || !next) return;
      const needImages = next.cards
        .filter((card) => {
          if (ids.length && !ids.includes(card.id)) return false;
          return Boolean(next!.features.image) && !imageDone(card);
        })
        .map((card) => card.id);
      if (needImages.length) {
        const meta = await api.meta();
        const imageWorkers = Math.max(1, Math.min(meta.defaults?.image_concurrency ?? 4, 64));
        await walk(needImages, imageWorkers, imageWorkers, true, needImages.length);
      }
      if (!alive.current || !next) return;
      const failed = Object.entries(next.stage_errors || {})
        .flatMap(([stage, list]) => list.map((item) => `${stage}: ${item}`));
      if (failed.length) setError(failed.slice(0, 6).join(" · "));
      else warnIfEmpty(next, ids);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("aiFail"));
    } finally {
      setBusy("");
      setProgress("");
    }
  }

  async function removeIds(ids: string[]) {
    if (!ids.length) return;
    setBusy("delete");
    setError("");
    try {
      const next = ids.length === 1
        ? await api.deleteCard(deckId, ids[0])
        : await api.bulkDeleteCards(deckId, ids);
      setDeck(next);
      setSelected([]);
      if (editing && ids.includes(editing.id)) setEditing(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("deleteFail"));
    } finally {
      setBusy("");
    }
  }

  async function saveCard(fields: Partial<Card>) {
    if (!editing) return;
    const next = await api.patchCard(deckId, editing.id, fields);
    setDeck(next);
    setEditing(next.cards.find((card) => card.id === editing.id) || null);
  }

  async function saveName() {
    if (!draftName.trim()) {
      setRenaming(false);
      return;
    }
    setDeck(await api.renameDeck(deckId, draftName));
    setRenaming(false);
  }

  async function removeDeck() {
    if (!window.confirm(t("confirmDeleteDeck"))) return;
    await api.deleteDeck(deckId);
    window.location.hash = "#/";
  }

  function toggle(id: string) {
    setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  }

  function toggleAllVisible() {
    const ids = visible.map((card) => card.id);
    const allOn = ids.every((id) => selected.includes(id));
    setSelected(allOn ? selected.filter((id) => !ids.includes(id)) : [...new Set([...selected, ...ids])]);
  }

  if (!deck) {
    return <p className="lede">{error || t("loadingDeck")}</p>;
  }

  const stats = coverage(deck);
  const qaReady = deck.stages.qa === "passed" || (
    stats.total > 0
    && stats.errors === 0
    && stats.french === stats.total
    && (!deck.features.phonetic || stats.phonetic === stats.total)
    && (!deck.features.phrase || stats.phrases === stats.total)
    && (!deck.features.audio || stats.audio === stats.total)
  );
  const stageNotes = Object.entries(deck.stage_errors || {}).flatMap(([stage, list]) =>
    list.map((item) => `${stage}: ${item}`),
  );
  const stagesOpen = STAGES.some((stage) => {
    const status = deck.stages[stage] || "pending";
    return status === "pending" || status === "failed" || status === "running";
  });

  return (
    <section>
      <a className="tag" href={`#/lang/${encodeURIComponent(deck.language)}`}>
        {deck.language_label || deck.language}
      </a>
      <div className="hero" style={{ marginBottom: 24 }}>
        <div>
          {renaming ? (
            <input
              className="title-input"
              value={draftName}
              autoFocus
              onChange={(e) => setDraftName(e.target.value)}
              onBlur={saveName}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveName();
                if (e.key === "Escape") setRenaming(false);
              }}
            />
          ) : (
            <h1
              className="deck-title"
              title={t("renameDeck")}
              onClick={() => { setDraftName(deck.name); setRenaming(true); }}
            >
              {deck.name}
            </h1>
          )}
          <p className="lede lang-line">
            <Flag code={deck.language} title={deck.language_label || deck.language} />
            {deck.language_label || deck.language}
            <span> → </span>
            <Flag code={deck.target_language || deck.target_label} title={deck.target_label} />
            {deck.target_label || t("translation")}
            {release && (
              <GithubStatus
                release={release}
                onLabel={t("onGithub")}
                offLabel={t("notOnGithub")}
              />
            )}
          </p>
        </div>
        <div className="deck-toolbar">
          <button className="btn toolbar-main" disabled={Boolean(busy)} onClick={() => askAi(selected)}>
            {busy === "ai" ? (progress || t("aiWorking")) : selected.length ? t("aiOnCards", { n: selected.length }) : t("completeAi")}
          </button>
          <div className="toolbar-group">
            <div className="tag">{t("exportGroup")}</div>
            <div className="toolbar-row">
              <button className="btn ghost" disabled={!qaReady || Boolean(busy)} onClick={() => exportFile("csv")}>CSV</button>
              <button className="btn ghost" disabled={!qaReady || Boolean(busy)} onClick={() => exportFile("apkg")}>
                {busy === "apkg" ? t("exportingApkg") : t("downloadApkg")}
              </button>
              <button className="btn brass" disabled={!qaReady || Boolean(busy)} onClick={sendAnki}>
                {busy === "anki" ? t("ankiSending") : "Anki"}
              </button>
              <button className="btn ghost" disabled={!qaReady || Boolean(busy)} onClick={publishGithub}>
                {busy === "publish" ? t("publishing") : t("publishGithub")}
              </button>
              {release?.published && (release.asset || "").endsWith(".apkg") && (
                <button className="btn ghost danger" disabled={Boolean(busy)} onClick={unpublishGithub}>
                  {busy === "unpublish" ? t("langUnpublishing") : t("unpublishDeck")}
                </button>
              )}
            </div>
          </div>
          <div className="toolbar-row toolbar-quiet">
            <button className="btn ghost" disabled={Boolean(busy)} onClick={() => run("all")}>
              {busy === "all" ? t("generating") : t("regenerateAll")}
            </button>
            <button className="btn ghost danger" onClick={removeDeck}>{t("deleteDeck")}</button>
          </div>
        </div>
      </div>

      <ImageTools key={deck.id} deck={deck} busy={Boolean(busy)} setBusy={setBusy} refresh={refresh} />

      {busy === "ai" && (
        <div className="fill-progress" role="status">
          <div className="fill-progress-label">{progress || t("aiWorking")}</div>
          <div className="fill-bar">
            <i style={{ width: `${Math.max(2, Math.round(((stats.total - stats.leftover) / Math.max(stats.total, 1)) * 100))}%` }} />
          </div>
          <div className="meta">
            {t("aiProgress", { done: stats.total - stats.leftover, total: stats.total })}
          </div>
        </div>
      )}

      <div className="fill-config">
        <div className="tag">{t("fillWhat")}</div>
        <div className="features">
          <label className="feature">
            <input
              type="checkbox"
              checked={deck.features.phonetic}
              disabled={Boolean(busy)}
              onChange={(e) => setFeature("phonetic", e.target.checked)}
            />
            <span><strong>{t("phonetic")}</strong><em>{t("phoneticHint")}</em></span>
          </label>
          <label className="feature">
            <input
              type="checkbox"
              checked={deck.features.phrase}
              disabled={Boolean(busy)}
              onChange={(e) => setFeature("phrase", e.target.checked)}
            />
            <span><strong>{t("phrase")}</strong><em>{t("phraseHint")}</em></span>
          </label>
          <label className="feature">
            <input
              type="checkbox"
              checked={deck.features.audio}
              disabled={Boolean(busy)}
              onChange={(e) => setFeature("audio", e.target.checked)}
            />
            <span><strong>{t("audio")}</strong><em>{t("audioHint")}</em></span>
          </label>
          <label className="feature">
            <input
              type="checkbox"
              checked={deck.features.image}
              disabled={Boolean(busy)}
              onChange={(e) => setFeature("image", e.target.checked)}
            />
            <span><strong>{t("image")}</strong><em>{t("imageHint")}</em></span>
          </label>
        </div>
      </div>

      <div className="dash">
        <Tile label={t("cards")} value={deck.cards.length} extra={<FeatureIcons features={deck.features} />} />
        <Tile label={t("translation")} value={stats.french} total={stats.total} />
        {deck.features.phonetic && <Tile label={t("phonetic")} value={stats.phonetic} total={stats.total} />}
        {(deck.features.phrase || stats.phrases > 0) && (
          <Tile label={t("phrase")} value={stats.phrases} total={stats.total} />
        )}
        {(deck.features.audio || stats.audio > 0) && (
          <Tile label={t("audio")} value={stats.audio} total={stats.total} />
        )}
        {(deck.features.image || stats.images > 0) && (
          <Tile label={t("image")} value={stats.images} total={stats.total} />
        )}
        <article className={`dash-tile ${stats.errors || stats.leftover ? "warn" : ""}`}>
          <div className="meta">{stats.errors ? t("filterErrors") : t("leftToFill")}</div>
          <strong>{stats.errors || stats.leftover}</strong>
          {stats.errors > 0 && (
            <button
              className="btn ghost dash-retry"
              type="button"
              disabled={Boolean(busy)}
              onClick={() => {
                setFilter("errors");
                const quality = deck.cards.filter(qualityError).map((card) => card.id);
                const stale = deck.cards.filter(retryableError).map((card) => card.id);
                askAi(quality.length ? quality : stale.slice(0, 1), true);
              }}
            >
              {busy === "ai" ? t("retryingErrors") : t("retryErrors")}
            </button>
          )}
        </article>
      </div>

      <div className="add-row">
        <textarea
          placeholder={t("addWordsPh")}
          aria-label={t("addWordsPh")}
          value={addText}
          onChange={(e) => setAddText(e.target.value)}
        />
        <button className="btn" disabled={busy === "add"} type="button" onClick={addWords}>
          {busy === "add" ? t("aiRunning") : t("addGenerate")}
        </button>
      </div>

      <div className="editor-bar">
        <input
          placeholder={t("searchCards")}
          aria-label={t("searchCards")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select value={filter} onChange={(e) => setFilter(parseFilter(e.target.value))}>
          <option value="all">{t("filterAll")}</option>
          <option value="missing">{t("filterMissing")}</option>
          <option value="errors">{t("filterErrors")}</option>
        </select>
        <span className="meta">{visible.length} / {deck.cards.length}</span>
      </div>

      {selected.length > 0 && (
        <div className="banner">
          {t("selected", { n: selected.length })}
          <button
            className="btn ghost"
            type="button"
            onClick={() => {
              if (window.confirm(t("confirmDeleteCards", { n: selected.length }))) removeIds(selected);
            }}
          >
            {t("deleteSelection")}
          </button>
        </div>
      )}

      {download && (
        <div className="banner">
          {t("exportReady")}<a href={download} download={download.split("/").pop()}>{t("download")} {download.split("/").pop()}</a>
        </div>
      )}
      {publishNote && (
        <div className="banner">
          {t("published")}{" "}
          {publishNote.startsWith("http") ? <a href={publishNote}>{publishNote}</a> : publishNote}
        </div>
      )}
      {ankiNote && <div className="banner">{ankiNote}</div>}
      {(error || stageNotes.length > 0) && (
        <div className="errors">
          {error}
          {stageNotes.length > 0 && (
            <ul className="error-list">
              {stageNotes.slice(0, 8).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <section className="card-gallery">
        <div className="card-gallery-head">
          <div className="tag">{t("previewCardsOf", { shown: Math.min(shown, visible.length), total: visible.length })}</div>
          {shown < visible.length && <div className="meta">{t("scrollMore")}</div>}
        </div>
        <div className="preview-grid">
          {visible.slice(0, shown).map((card) => (
            <div key={`pv-${card.id}`} className="card-wrap">
              <FlipCard card={card} targetLabel={deck.target_label || t("translation")} features={deck.features} />
              <button className="btn ghost" type="button" onClick={() => setEditing(card)}>{t("edit")}</button>
            </div>
          ))}
        </div>
        {shown < visible.length && <div ref={sentinel} className="gallery-sentinel" aria-hidden="true" />}
      </section>

      <details className="word-drawer">
        <summary>
          {t("wordList", { shown: visible.length, total: deck.cards.length })}
          {visible.length > 8 ? t("wordListHint") : ""}
        </summary>
        <div className="table-wrap table-limited">
          <table>
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    checked={visible.length > 0 && visible.every((card) => selected.includes(card.id))}
                    onChange={toggleAllVisible}
                  />
                </th>
                <th>{t("word")}</th>
                <th>{deck.target_label || t("translationShort")}</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {visible.map((card) => (
                <tr key={card.id}>
                  <td>
                    <input type="checkbox" checked={selected.includes(card.id)} onChange={() => toggle(card.id)} />
                  </td>
                  <td>
                    <strong>{card.surface}</strong>
                    {retryableError(card) && <div className="meta">{t("review")}</div>}
                  </td>
                  <td>{card.french || "—"}</td>
                  <td className="row-actions">
                    <button className="btn ghost" type="button" onClick={() => setEditing(card)}>{t("edit")}</button>
                    <button
                      className="btn ghost"
                      type="button"
                      onClick={() => {
                        if (window.confirm(t("confirmDeleteCard", { word: card.surface }))) removeIds([card.id]);
                      }}
                    >
                      {t("delete")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>

      <details className="log-drawer" open={stagesOpen}>
        <summary>
          {t("stageLog")}
          {stats.leftover ? ` · ${stats.leftover} ${t("leftToFill")}` : ""}
        </summary>
        <div className="stages">
          {STAGES.map((stage, index) => {
            const status = deck.stages[stage] || "pending";
            const notes = (deck.stage_errors || {})[stage] || [];
            return (
              <div key={stage}>
                <button
                  className={`stage ${status}`}
                  disabled={Boolean(busy) || status === "skipped"}
                  onClick={() => run(stage)}
                >
                  <span className="num">{String(index + 1).padStart(2, "0")}</span>
                  <span>{t(STAGE_LABEL_KEYS[stage])}</span>
                  <span className="meta">{status}</span>
                </button>
                {notes.length > 0 && (
                  <p className="stage-error">{notes[0]}</p>
                )}
              </div>
            );
          })}
        </div>
      </details>

      {editing && (
        <CardEditor
          deck={deck}
          card={editing}
          onClose={() => setEditing(null)}
          onSave={saveCard}
          onDelete={async () => { await removeIds([editing.id]); }}
          onEnrich={async () => {
            const next = await api.enrichCards(deckId, [editing.id], 1, deck.features, 1, true, true);
            setDeck(next);
            setEditing(next.cards.find((card) => card.id === editing.id) || null);
          }}
        />
      )}
    </section>
  );
}
