import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api, type Meta, type Settings } from "../api";
import { LanguageSelect } from "../components/LanguageSelect";
import { FlipCard, previewCard } from "../components/FlipCard";
import { useLocale } from "../i18n/LocaleContext";

export function NewDeck() {
  const { t } = useLocale();
  const [meta, setMeta] = useState<Meta | null>(null);
  const [name, setName] = useState("");
  const [languageQuery, setLanguageQuery] = useState("");
  const [targetQuery, setTargetQuery] = useState("Français");
  const [sourceId, setSourceId] = useState("custom");
  const [limit, setLimit] = useState(1000);
  const [words, setWords] = useState("");
  const [phrase, setPhrase] = useState(true);
  const [phonetic, setPhonetic] = useState(true);
  const [audio, setAudio] = useState(false);
  const [image, setImage] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.meta(), api.settings()])
      .then(([data, settings]: [Meta, Settings]) => {
        setMeta(data);
        setTargetQuery(settings.default_target_label || data.defaults?.target_label || "English");
        const raw = window.location.hash.replace(/^#/, "");
        const query = raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : "";
        const preset = new URLSearchParams(query).get("lang") || "";
        if (preset) {
          const hit = data.languages.find((item) => item.id === preset || item.label.toLowerCase() === preset.toLowerCase());
          if (hit) setLanguageQuery(hit.label);
        }
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  const matched = useMemo(() => {
    const needle = languageQuery.trim().toLowerCase();
    const list = meta?.languages || [];
    return list.find((item) => item.id === needle || item.label.toLowerCase() === needle);
  }, [languageQuery, meta]);

  const matchedTarget = useMemo(() => {
    const needle = targetQuery.trim().toLowerCase();
    const list = meta?.languages || [];
    return list.find((item) => item.id === needle || item.label.toLowerCase() === needle);
  }, [targetQuery, meta]);

  const strategies = useMemo(() => {
    const list = meta?.strategies || [];
    if (!matched) return [];
    const mine = list.filter((item) => item.language === matched.id);
    return [
      ...mine.filter((item) => item.kind !== "grammar"),
      ...mine.filter((item) => item.kind === "grammar"),
    ];
  }, [meta, matched]);

  const selectedStrategy = strategies.find((item) => item.id === sourceId);
  const sizes = selectedStrategy?.sizes || [];
  const available = selectedStrategy?.available || 0;

  useEffect(() => {
    setSourceId(strategies[0]?.id || "custom");
  }, [matched?.id]);

  useEffect(() => {
    if (!selectedStrategy) return;
    setPhrase(selectedStrategy.phrase);
    setAudio(selectedStrategy.audio);
    setImage(false);
    const starter = [500, 1000, 2000].find((n) => n <= selectedStrategy.available);
    setLimit(starter || selectedStrategy.available);
  }, [selectedStrategy?.id]);

  const firstWord = words.split(/\n|,/).map((item) => item.trim()).find(Boolean) || "";
  const features = { phrase, phonetic, audio, image };
  const sample = previewCard(firstWord, matchedTarget?.label || targetQuery || "Français", features);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const list = words.split(/\n|,/).map((item) => item.trim()).filter(Boolean);
    if (!languageQuery.trim()) {
      setError(t("needLanguage"));
      return;
    }
    if (sourceId === "custom" && !list.length) {
      setError(t("needWords"));
      return;
    }
    setBusy(true);
    setError("");
    try {
      const strategy = selectedStrategy;
      const sizeLabel = strategy && limit >= (strategy.available || 0) ? t("all") : `${limit}`;
      const deck = await api.createDeck({
        name: name.trim() || (strategy
          ? `${strategy.label} ${sizeLabel}`
          : `Deck ${matched?.label || languageQuery.trim()}`),
        language: matched?.id || languageQuery.trim(),
        language_label: matched?.label || languageQuery.trim(),
        target_language: matchedTarget?.id || targetQuery.trim(),
        target_label: matchedTarget?.label || targetQuery.trim(),
        words: sourceId === "custom" ? list : [],
        source_id: sourceId === "custom" ? "" : sourceId,
        limit: sourceId === "custom" ? null : limit,
        features: { phrase, phonetic, audio, image },
      });
      window.location.hash = `#/deck/${deck.id}`;
    } catch (err) {
      setError(err instanceof Error ? err.message : t("createFail"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <div className="tag">{t("createTag")}</div>
      <h1 className="display">{t("createTitle")}</h1>
      <p className="lede">{t("createLede")}</p>
      <div className="create-layout">
      <form className="form" onSubmit={submit}>
        <label>
          {t("deckName")}
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("deckNamePh")} />
        </label>
        <div className="field">
          <span>{t("learnLanguage")}</span>
          <LanguageSelect
            languages={meta?.languages || []}
            value={languageQuery}
            onChange={(label) => setLanguageQuery(label)}
            placeholder={t("learnLanguagePh")}
            required
          />
        </div>
        <div className="field">
          <span>{t("translateTo")}</span>
          <LanguageSelect
            languages={meta?.languages || []}
            value={targetQuery}
            onChange={(label) => setTargetQuery(label)}
            placeholder={t("translateToPh")}
          />
        </div>
        <label>
          {t("strategy")}
          <select value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
            <option value="custom">{t("customList")}</option>
            {strategies.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label} ({t("wordsAvailable", { n: item.available })})
              </option>
            ))}
          </select>
        </label>
        {selectedStrategy && (
          <div className="field">
            <span>{t("deckSize")}</span>
            <p className="size-hint">{t("sizeHint")}</p>
            <div className="size-presets">
              {sizes.filter((size) => size < available).map((size) => (
                <button
                  key={size}
                  type="button"
                  className={`chip ${limit === size ? "on" : ""}`}
                  onClick={() => setLimit(size)}
                >
                  {t("sizeWords", { n: size.toLocaleString() })}
                </button>
              ))}
              <button
                type="button"
                className={`chip ${limit >= available ? "on" : ""}`}
                onClick={() => setLimit(available)}
              >
                {t("sizeAll", { n: available.toLocaleString() })}
              </button>
            </div>
            <input
              type="number"
              min={1}
              max={available}
              value={limit}
              onChange={(e) => {
                const next = Number(e.target.value);
                if (!Number.isFinite(next)) return;
                setLimit(Math.min(available, Math.max(1, Math.round(next))));
              }}
            />
            <p className="size-coverage">
              {limit >= available
                ? t("sizeCoverageAll", { n: available.toLocaleString() })
                : limit <= 500
                  ? t("sizeCoverage500")
                  : limit <= 1000
                    ? t("sizeCoverage1000")
                    : limit <= 2000
                      ? t("sizeCoverage2000")
                      : t("sizeCoverageMore")}
            </p>
            {sourceId.startsWith("spoken-") && <p className="size-hint">{t("spokenNote")}</p>}
          </div>
        )}
        {sourceId === "custom" && (
          <label>
            {t("wordsOnePerLine")}
            <textarea
              value={words}
              onChange={(e) => setWords(e.target.value)}
              placeholder={"ciao\ngrazie\nper favore"}
            />
          </label>
        )}
        <div>
          <div className="tag" style={{ marginBottom: 10 }}>{t("cardContent")}</div>
          <div className="features">
            <label className="feature">
              <input type="checkbox" checked={phonetic} onChange={(e) => setPhonetic(e.target.checked)} />
              <span><strong>{t("phonetic")}</strong><em>{t("phoneticHint")}</em></span>
            </label>
            <label className="feature">
              <input type="checkbox" checked={phrase} onChange={(e) => setPhrase(e.target.checked)} />
              <span><strong>{t("phrase")}</strong><em>{t("phraseHint")}</em></span>
            </label>
            <label className="feature">
              <input type="checkbox" checked={audio} onChange={(e) => setAudio(e.target.checked)} />
              <span><strong>{t("audio")}</strong><em>{t("audioHint")}</em></span>
            </label>
            <label className="feature">
              <input type="checkbox" checked={image} onChange={(e) => setImage(e.target.checked)} />
              <span><strong>{t("image")}</strong><em>{t("imageHint")}</em></span>
            </label>
          </div>
        </div>
        {error && <div className="errors">{error}</div>}
        <div className="actions">
          <button className="btn" disabled={busy} type="submit">
            {busy ? t("aiFilling") : t("createGenerate")}
          </button>
        </div>
      </form>
      <aside className="preview-pane">
        <div className="tag">{t("preview")}</div>
        <p className="meta">{t("previewHint")}</p>
        <FlipCard
          card={sample}
          targetLabel={matchedTarget?.label || targetQuery || "Français"}
          features={features}
        />
      </aside>
      </div>
    </section>
  );
}
