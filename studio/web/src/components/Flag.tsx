import { countryCodeForLanguage, flagUrl } from "../i18n/flags";

export function Flag({ code, title }: { code: string; title?: string }) {
  const src = flagUrl(code, 20);
  const cc = countryCodeForLanguage(code);
  if (!src) {
    return (
      <span className="flag flag-empty" title={title || code} aria-hidden>
        🌐
      </span>
    );
  }
  return (
    <img
      className="flag"
      alt=""
      title={title || cc.toUpperCase()}
      src={src}
      srcSet={`${flagUrl(code, 40)} 2x`}
    />
  );
}
