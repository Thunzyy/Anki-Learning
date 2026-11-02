import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { runDeckBatch, type BatchDeck, type BatchResult } from "../deckBatch";
import { useLocale } from "../i18n/LocaleContext";

export function DeckBulkActions() {
  const { t } = useLocale();
  const [mode, setMode] = useState<"anki" | "github">("anki");
  const [destination, setDestination] = useState("");
  const running = useRef(false);
  const stop = useRef(false);
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [opened, setOpened] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState<BatchResult[]>([]);
  const [pending, setPending] = useState<BatchDeck[]>([]);
  const [progress, setProgress] = useState({ completed: 0, total: 0, name: "" });

  useEffect(() => {
    if (!busy) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [busy]);

  async function run(target: "anki" | "github", retry?: BatchDeck[]) {
    if (running.current) return;
    running.current = true;
    setMode(target);
    setDestination("");
    stop.current = false;
    setBusy(true);
    setStopping(false);
    setOpened(true);
    setError("");
    setResults([]);
    setProgress({ completed: 0, total: 0, name: "" });
    let queue = retry || [];
    setPending(queue);
    try {
      if (target === "anki") {
        const status = await api.ankiStatus();
        if (!status.ok) throw new Error(t("ankiClosed", { url: status.url }));
      } else {
        const status = await api.listPublishAssets();
        if (!status.ok) throw new Error(t("githubAllFail"));
        setDestination(status.url);
      }
      queue = retry || await api.decks();
      setPending(queue);
      setProgress({ completed: 0, total: queue.length, name: "" });
      const finished = await runDeckBatch(queue, target === "anki" ? api.sendToAnki : api.publishDeck,
        (completed, total, deck) => setProgress({ completed, total, name: deck.name }),
        (result) => {
          setResults((previous) => [...previous, result]);
          setProgress((previous) => ({ ...previous, completed: previous.completed + 1 }));
        },
        () => stop.current,
      );
      const successful = new Set(finished.filter((result) => result.ok).map((result) => result.deck.id));
      setPending(queue.filter((deck) => !successful.has(deck.id)));
    } catch (err) {
      setError(err instanceof Error ? err.message : t(target === "anki" ? "ankiFail" : "githubAllFail"));
    } finally {
      running.current = false;
      setBusy(false);
    }
  }

  const sent = results.filter((result) => result.ok).length;
  const failures = results.filter((result) => !result.ok);
  return <section className="anki-bulk" aria-label={t("bulkActions")}>
    <div className="anki-bulk-actions">
      <button className="btn brass" disabled={busy} onClick={() => void run("anki")}>
        {busy && mode === "anki" ? t("langSendingAll", { done: progress.completed, total: progress.total }) : t("ankiAll")}
      </button>
      <button className="btn" disabled={busy} onClick={() => void run("github")}>
        {busy && mode === "github" ? t("githubAllProgress", { done: progress.completed, total: progress.total }) : t("githubAll")}
      </button>
      <span className="muted">{t("bulkHint")}</span>
      {busy && <button className="btn ghost" disabled={stopping} onClick={() => {
        stop.current = true;
        setStopping(true);
      }}>{t(stopping ? "ankiAllStopping" : "ankiAllStop")}</button>}
    </div>
    {opened && <div className="anki-bulk-report">
      <div role="status" aria-live="polite">
        {busy ? <>
          <p>{progress.name || t(mode === "anki" ? "ankiAllChecking" : "githubAllChecking")}</p>
          <progress aria-label={t(mode === "anki" ? "ankiAll" : "githubAll")} value={progress.completed} max={Math.max(1, progress.total)} />
          <p className="muted">{t("ankiAllKeepOpen")}</p>
        </> : !error && <p>{t(mode === "anki" ? "ankiAllResult" : "githubAllResult", { sent, failed: failures.length, remaining: pending.length - failures.length })}</p>}
      </div>
      {mode === "github" && destination && <p><a href={destination} target="_blank" rel="noreferrer">{t("githubAllDestination")}</a></p>}
      {results.some((result) => result.warnings?.length) && <ul>{results.flatMap((result) =>
        (result.warnings || []).map((warning, index) => <li key={`${result.deck.id}-${index}`}>{result.deck.name} — {warning}</li>)
      )}</ul>}
      {error && <p role="alert" className="errors">{error}</p>}
      {failures.length > 0 && <ul>{failures.map((result) => <li key={result.deck.id}>
        <a href={`#/deck/${result.deck.id}`}>{result.deck.name}</a> — {result.error}
      </li>)}</ul>}
      {!busy && <div className="anki-bulk-actions">
        {pending.length > 0 && <button className="btn" onClick={() => void run(mode, pending)}>{t("ankiAllRetry", { n: pending.length })}</button>}
        <button className="btn ghost" onClick={() => setOpened(false)}>{t("ankiAllDismiss")}</button>
      </div>}
    </div>}
  </section>;
}
