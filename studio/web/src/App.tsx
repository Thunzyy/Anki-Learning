import { useEffect, useMemo, useState } from "react";
import { Home } from "./pages/Home";
import { LanguagePage } from "./pages/Language";
import { NewDeck } from "./pages/NewDeck";
import { Workshop } from "./pages/Workshop";
import { SettingsPage } from "./pages/Settings";
import { Flag } from "./components/Flag";
import { DeckBulkActions } from "./components/DeckBulkActions";
import { useLocale } from "./i18n/LocaleContext";
import { UI_LANGS } from "./i18n/translations";

const NAV = [
  { href: "#/", labelKey: "navDecks", match: (path: string) => path === "/" || path.startsWith("/lang") || path.startsWith("/deck") },
  { href: "#/new", labelKey: "navNew", match: (path: string) => path === "/new" },
  { href: "#/settings", labelKey: "navSettings", match: (path: string) => path === "/settings" },
] as const;

function parseHash() {
  const raw = window.location.hash.replace(/^#/, "") || "/";
  const [path, query = ""] = raw.split("?");
  const parts = path.split("/").filter(Boolean);
  return { path: `/${parts.join("/")}`, parts, query };
}

export function App() {
  const { lang, setLang, t } = useLocale();
  const [route, setRoute] = useState(parseHash);
  const [flagsOpen, setFlagsOpen] = useState(false);

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (!flagsOpen) return;
    const close = () => setFlagsOpen(false);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setFlagsOpen(false);
    };
    window.addEventListener("click", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [flagsOpen]);

  const view = useMemo(() => {
    if (route.path === "/new") return <NewDeck />;
    if (route.path === "/settings") return <SettingsPage />;
    if (route.parts[0] === "lang" && route.parts[1]) {
      return <LanguagePage langId={route.parts[1]} />;
    }
    if (route.parts[0] === "deck" && route.parts[1]) {
      return <Workshop deckId={route.parts[1]} />;
    }
    return <Home />;
  }, [route]);

  return (
    <div className="shell">
      <header className="topbar">
        <a href="#/" className="brand" aria-label={t("appName")}>
          <span className="mark">{t("appName")}</span>
          <span className="tag">{t("appTag")}</span>
        </a>
        <nav className="nav" aria-label={t("navLabel")}>
          {NAV.map((item) => (
            <a
              key={item.href}
              href={item.href}
              aria-current={item.match(route.path) ? "page" : undefined}
            >
              {t(item.labelKey)}
            </a>
          ))}
          <span className="nav-sep" aria-hidden="true" />
          <div className="ui-lang" onClick={(event) => event.stopPropagation()}>
            <button
              type="button"
              className="ui-lang-btn"
              aria-label={t("uiLanguage")}
              aria-expanded={flagsOpen}
              onClick={() => setFlagsOpen((open) => !open)}
            >
              <Flag code={lang} />
              <span className="ui-lang-code">{lang.toUpperCase()}</span>
            </button>
            {flagsOpen && (
              <div className="ui-lang-pop" role="listbox" aria-label={t("uiLanguage")}>
                {UI_LANGS.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    role="option"
                    aria-selected={item.id === lang}
                    className={`ui-lang-opt ${item.id === lang ? "on" : ""}`}
                    title={item.label}
                    onClick={() => {
                      setLang(item.id);
                      setFlagsOpen(false);
                    }}
                  >
                    <Flag code={item.id} title={item.label} />
                  </button>
                ))}
              </div>
            )}
          </div>
        </nav>
      </header>
      <DeckBulkActions />
      {view}
    </div>
  );
}
