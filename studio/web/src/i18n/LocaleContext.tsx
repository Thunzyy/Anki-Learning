import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { translations, UI_LANGS, type MsgKey, type UiLang } from "./translations";

const STORAGE = "learning-ui-lang";

type LocaleCtx = {
  lang: UiLang;
  setLang: (lang: UiLang) => void;
  t: (key: MsgKey, vars?: Record<string, string | number>) => string;
};

const Ctx = createContext<LocaleCtx | null>(null);

function readStored(): UiLang {
  try {
    const raw = localStorage.getItem(STORAGE) || "";
    const match = UI_LANGS.find((item) => item.id === raw);
    if (match) return match.id;
  } catch {
    /* ignore */
  }
  return "en";
}

function fill(template: string, vars?: Record<string, string | number>) {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ""));
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<UiLang>(readStored);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const value = useMemo<LocaleCtx>(() => {
    const table = translations[lang];
    return {
      lang,
      setLang: (next) => {
        setLangState(next);
        try {
          localStorage.setItem(STORAGE, next);
        } catch {
          /* ignore */
        }
      },
      t: (key, vars) => fill(table[key] || translations.en[key] || key, vars),
    };
  }, [lang]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useLocale() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useLocale outside LocaleProvider");
  return ctx;
}
