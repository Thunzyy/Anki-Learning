import type { Deck } from "../api";
import { useLocale } from "../i18n/LocaleContext";
import { STAGES } from "../stages";
import { FeatureIcons } from "./FeatureIcons";

export function StageBar({ deck }: { deck: Deck }) {
  return (
    <div className="pips" aria-hidden>
      {STAGES.map((stage) => (
        <i key={stage} className={`pip ${deck.stages[stage] || ""}`} title={`${stage}: ${deck.stages[stage] || "pending"}`} />
      ))}
    </div>
  );
}

export function DeckGrid({ decks }: { decks: Deck[] }) {
  const { t } = useLocale();
  return (
    <div className="grid">
      {decks.map((deck) => (
        <a className="card" key={deck.id} href={`#/deck/${deck.id}`}>
          <div>
            <div className="meta">
              {t("cardsCount", { n: deck.card_count ?? deck.cards.length })}
              <FeatureIcons features={deck.features} />
            </div>
            <h3>{deck.name}</h3>
          </div>
          <StageBar deck={deck} />
        </a>
      ))}
    </div>
  );
}
