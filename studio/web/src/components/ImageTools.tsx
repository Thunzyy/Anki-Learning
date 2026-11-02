import { useRef, useState } from "react";
import { api, type Deck, type ImagePlan } from "../api";
import { useLocale } from "../i18n/LocaleContext";

const SOURCES = ["grok", "gemini", "openai", "wikimedia", "unsplash", "pexels", "generated", "unknown", "manual"];

export function ImageTools({ deck, busy, setBusy, refresh }: {
  deck: Deck; busy: boolean; setBusy: (value: string) => void; refresh: () => Promise<Deck>;
}) {
  const { t } = useLocale();
  const [source, setSource] = useState("all");
  const [destination, setDestination] = useState("library");
  const [generate, setGenerate] = useState(false);
  const [plan, setPlan] = useState<ImagePlan | null>(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const stop = useRef(false);
  const active = useRef(false);

  function clear() { setPlan(null); setStatus(""); setError(""); }
  async function preview() {
    setBusy("images"); setError(""); setStatus(""); setPlan(null);
    try {
      setPlan(await api.replaceImages(deck.id, { source, destination, generate_missing: generate, preview: true }));
    } catch (err) { setError(String(err)); }
    finally { setBusy(""); }
  }
  async function apply() {
    if (!plan) return;
    setBusy("images"); setError(""); stop.current = false; active.current = true;
    let changed = 0;
    let revision = plan.revision;
    const failures: string[] = [];
    // Freeze the preview IDs. Never re-select an already replaced card in a later batch.
    const ids = plan.items.filter(item => item.action !== "unmatched").map(item => item.id);
    try {
      for (let offset = 0; offset < ids.length && !stop.current; offset += 10) {
        const result = await api.replaceImages(deck.id, { source, destination, generate_missing: generate,
          preview: false, ids: ids.slice(offset, offset + 10), limit: 10, expected_revision: revision });
        revision = result.revision;
        changed += result.changed;
        failures.push(...result.errors.map(item => `${item.id}: ${item.message}`));
        setStatus(`${t("imageChanged")}: ${changed} · ${Math.min(offset + 10, ids.length)} / ${ids.length}`);
        await refresh();
      }
      setPlan(null);
      if (failures.length) setError(failures.join("\n"));
    } catch (err) { setError(String(err)); }
    finally { active.current = false; setBusy(""); await refresh().catch(err => setError(String(err))); }
  }
  return <details className="fill-config image-tools">
    <summary>{t("imageManage")}</summary>
    <p className="meta">{t("imageReuseHint")}</p>
    <div className="actions">
      <label>{t("imageFrom")}
        <select disabled={busy} value={source} onChange={e => { setSource(e.target.value); clear(); }}>
          <option value="all">{t("imageAll")}</option>
          <option value="missing">{t("imageMissing")}</option>
          {SOURCES.map(item => <option key={item} value={item}>{item === "generated" ? t("imageLegacy") : item}</option>)}
        </select>
      </label>
      <label>{t("imageTo")}
        <select disabled={busy} value={destination} onChange={e => { setDestination(e.target.value); clear(); }}>
          <option value="library">{t("imageLibrary")}</option>
          {["grok", "gemini", "openai", "wikimedia", "unsplash", "pexels"].map(item => <option key={item} value={item}>{item === "openai" ? "Codex (OpenAI)" : item}</option>)}
          <option value="remove">{t("imageRemove")}</option>
        </select>
      </label>
    </div>
    {["grok", "openai", "wikimedia", "unsplash", "pexels"].includes(destination) && <label className="image-generate">
      <input type="checkbox" checked={generate} disabled={busy} onChange={e => { setGenerate(e.target.checked); clear(); }} />
      {t("imageGenerate")}
    </label>}
    <div className="actions">
      <button className="btn ghost" disabled={busy} onClick={preview}>{t("imagePreview")}</button>
      {plan && <button className="btn" disabled={busy || !plan.items.some(p => p.action !== "unmatched")}
        onClick={apply}>{t("imageApply")} ({plan.total - plan.unmatched})</button>}
      {busy && active.current && <button className="btn ghost" onClick={() => { stop.current = true; }}>{t("imageStop")}</button>}
    </div>
    {plan && <>
      <p>{plan.total} · {t("imageMatched")}: {plan.matched} · {t("imageUnmatched")}: {plan.unmatched}</p>
      <div className="image-plan">
        {plan.items.slice(0, 12).map(item => <article key={item.id}>
          <strong>{item.word}</strong>
          <span className="meta">{item.before.source} → {item.replacement?.source || item.action}</span>
          <div>{item.before.url && <img src={item.before.url} alt={`${item.word} (${item.before.source})`} loading="lazy" />}
            {item.replacement && <img src={item.replacement.url} alt={`${item.word} (${item.replacement.source})`} loading="lazy" />}</div>
        </article>)}
      </div>
    </>}
    {status && <p role="status">{status}</p>}
    {error && <p className="errors" role="alert">{error}</p>}
  </details>;
}
