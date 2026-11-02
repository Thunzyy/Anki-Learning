import type { DeckFeatures } from "../api";
import { useLocale } from "../i18n/LocaleContext";
import type { MsgKey } from "../i18n/translations";

const MARKS = [
  { key: "phonetic", labelKey: "featPhonetic", glyph: "/ə/" },
  { key: "phrase", labelKey: "featPhrase", glyph: "«»" },
  { key: "audio", labelKey: "featAudio", glyph: "♪" },
  { key: "image", labelKey: "featImage", glyph: "▣" },
] as const satisfies ReadonlyArray<{ key: keyof DeckFeatures; labelKey: MsgKey; glyph: string }>;

export function FeatureIcons({ features }: { features?: DeckFeatures | null }) {
  const { t } = useLocale();
  if (!features) return null;
  const active = MARKS.filter((item) => features[item.key]);
  if (!active.length) return null;
  return (
    <span className="feat-icons" aria-label={active.map((item) => t(item.labelKey)).join(", ")}>
      {active.map((item) => (
        <span key={item.key} className="feat-icon" title={t(item.labelKey)}>
          {item.glyph}
        </span>
      ))}
    </span>
  );
}
