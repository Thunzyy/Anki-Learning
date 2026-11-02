import { useEffect, useMemo, useRef, useState } from "react";
import { useLocale } from "../i18n/LocaleContext";
import { Flag } from "./Flag";

export type LangOption = { id: string; label: string };

function fold(value: string) {
  return value
    .trim()
    .toLowerCase()
    .normalize("NFD")
    .replace(/\p{M}/gu, "");
}

export function LanguageSelect({
  languages,
  value,
  onChange,
  placeholder,
  required,
}: {
  languages: LangOption[];
  value: string;
  onChange: (label: string, id: string) => void;
  placeholder: string;
  required?: boolean;
}) {
  const { t } = useLocale();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const selected = useMemo(
    () => languages.find((item) => item.label === value || item.id === value.toLowerCase()),
    [languages, value],
  );
  const filtered = useMemo(() => {
    const needle = fold(query);
    if (!needle) return languages;
    return languages.filter((item) => {
      const hay = `${fold(item.label)} ${fold(item.id)}`;
      return hay.includes(needle) || fold(item.label).split(/\s+/).some((part) => part.startsWith(needle));
    });
  }, [languages, query]);

  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [open]);

  useEffect(() => {
    if (!open) {
      setQuery("");
      return;
    }
    searchRef.current?.focus();
  }, [open]);

  const shown = selected?.label || value;

  function pick(item: LangOption) {
    onChange(item.label, item.id);
    setOpen(false);
  }

  return (
    <div className="lang-dropdown" onClick={(event) => event.stopPropagation()}>
      <button
        type="button"
        className={`lang-dropdown-btn ${shown ? "" : "is-empty"}`}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-required={required}
        onClick={() => setOpen((current) => !current)}
      >
        {selected && <Flag code={selected.id} title={selected.label} />}
        <span className="lang-dropdown-label">{shown || placeholder}</span>
        <em aria-hidden>▾</em>
      </button>
      {open && (
        <div className="lang-dropdown-panel">
          <div className="lang-dropdown-search">
            <input
              ref={searchRef}
              type="search"
              value={query}
              placeholder={t("langSearch")}
              aria-label={t("langSearch")}
              autoComplete="off"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.preventDefault();
                  setOpen(false);
                  return;
                }
                if (event.key === "Enter") {
                  event.preventDefault();
                  if (filtered[0]) pick(filtered[0]);
                }
              }}
            />
          </div>
          <div className="lang-dropdown-list" role="listbox">
            {filtered.length === 0 && (
              <div className="lang-dropdown-empty">{t("langSearchEmpty")}</div>
            )}
            {filtered.map((item) => (
              <button
                key={item.id}
                type="button"
                role="option"
                className={`lang-dropdown-opt ${selected?.id === item.id ? "on" : ""}`}
                aria-selected={selected?.id === item.id}
                onClick={() => pick(item)}
              >
                <Flag code={item.id} title={item.label} />
                <span className="lang-dropdown-label">{item.label}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
