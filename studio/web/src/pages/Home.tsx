import { useEffect, useMemo, useState } from "react";
import { api, type Deck } from "../api";
import { DeckGrid } from "../components/DeckGrid";
import { Flag } from "../components/Flag";
import { useLocale } from "../i18n/LocaleContext";

function folderKey(deck: Deck) {
  return deck.language || deck.language_label || "autre";
}

function folderLabel(deck: Deck) {
  return deck.language_label || deck.language || "";
}

export function Home() {
  const { t, lang } = useLocale();
  const [decks, setDecks] = useState<Deck[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filterLang, setFilterLang] = useState("all");
  const [imageStats, setImageStats] = useState<{ images: number; illustrated_cards: number } | null>(null);
  const [imageStatsFailed, setImageStatsFailed] = useState(false);

  useEffect(() => {
    api.decks().then(setDecks).catch((err: Error) => {
      setDecks([]);
      setError(err.message);
    });
    api.libraryImageStats().then(setImageStats).catch(() => setImageStatsFailed(true));
  }, []);

  const all = decks || [];

  // Decks of one language can carry different labels ("pt" vs "Portugais (Brésil)");
  // keep the richest one so chips and folders always read the same.
  const labels = useMemo(() => {
    const map = new Map<string, string>();
    for (const deck of all) {
      const key = folderKey(deck);
      const label = folderLabel(deck) || t("other");
      const current = map.get(key);
      if (!current || current === key || current.length < label.length) map.set(key, label);
    }
    return map;
  }, [all, t]);

  const languages = useMemo(
    () => [...labels.entries()].sort((a, b) => a[1].localeCompare(b[1], lang)),
    [labels, lang],
  );

  const totalCards = useMemo(
    () => all.reduce((sum, deck) => sum + (deck.card_count ?? deck.cards.length), 0),
    [all],
  );

  const folders = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const groups = new Map<string, { key: string; label: string; decks: Deck[] }>();
    for (const deck of all) {
      const key = folderKey(deck);
      if (filterLang !== "all" && key !== filterLang) continue;
      const blob = `${deck.name} ${deck.language_label} ${deck.language}`.toLowerCase();
      if (needle && !blob.includes(needle)) continue;
      if (!groups.has(key)) groups.set(key, { key, label: labels.get(key) || t("other"), decks: [] });
      groups.get(key)!.decks.push(deck);
    }
    return [...groups.values()].sort((a, b) => a.label.localeCompare(b.label, lang));
  }, [all, query, filterLang, labels, t, lang]);

  return (
    <section>
      <div className="hero">
        <div>
          <div className="tag">{t("homeTag")}</div>
          <h1>{t("homeTitle")}</h1>
          <p className="lede">{t("homeLede")}</p>
          <div className="actions" style={{ marginTop: 22 }}>
            <a className="btn" href="#/new">{t("newDeck")}</a>
          </div>
        </div>
        <aside className="ticket">
          <div className="tag">{t("libraryTag")}</div>
          <div className="ticket-rows">
            <div className="ticket-row">
              <span>{t("statDecks")}</span>
              <b>{all.length}</b>
            </div>
            <div className="ticket-row">
              <span>{t("statLanguages")}</span>
              <b>{languages.length}</b>
            </div>
            <div className="ticket-row">
              <span>{t("statCards")}</span>
              <b>{totalCards.toLocaleString(lang)}</b>
            </div>
            <div className="ticket-row" title={t("statImagesHint")}>
              <span>{t("statImages")}</span>
              <b>{imageStats ? imageStats.images.toLocaleString(lang) : imageStatsFailed ? "—" : "…"}</b>
            </div>
          </div>
          <p className="library-image-note">{imageStatsFailed ? t("statImagesUnavailable") : t("statImagesHint")}</p>
        </aside>
      </div>

      <div className="library-bar">
        <input
          placeholder={t("filterPlaceholder")}
          aria-label={t("filterPlaceholder")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="lang-chips">
          <button type="button" className={`chip ${filterLang === "all" ? "on" : ""}`} onClick={() => setFilterLang("all")}>
            {t("all")}
          </button>
          {languages.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`chip ${filterLang === key ? "on" : ""}`}
              onClick={() => setFilterLang(key)}
            >
              <Flag code={key} title={label} />
              {label}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="errors">{error}</div>}

      {decks === null && (
        <div className="skeleton-grid">
          {[0, 1, 2, 3, 4, 5].map((slot) => (
            <div key={slot} className="skeleton" />
          ))}
        </div>
      )}

      {folders.map((folder) => {
        const cards = folder.decks.reduce((sum, deck) => sum + (deck.card_count ?? deck.cards.length), 0);
        return (
          <details key={folder.key} className="lang-folder" open>
            <summary>
              <a
                className="folder-title"
                href={`#/lang/${folder.key}`}
                onClick={(event) => event.stopPropagation()}
              >
                <Flag code={folder.key} title={folder.label} />
                {folder.label}
              </a>
              <span className="meta">{t("folderMeta", { decks: folder.decks.length, cards })}</span>
            </summary>
            <DeckGrid decks={folder.decks} />
          </details>
        );
      })}

      {decks !== null && folders.length === 0 && !error && (
        <div className="card">
          <div>
            <div className="meta">{t("empty")}</div>
            <h3>{t("noDeck")}</h3>
            <p className="lede">{t("noDeckHint")}</p>
          </div>
          <a className="btn" href="#/new" style={{ justifySelf: "start" }}>{t("newDeck")}</a>
        </div>
      )}
    </section>
  );
}
