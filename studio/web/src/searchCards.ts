export type SearchableCard = {
  lemma: string;
  surface: string;
  french: string;
  phrase?: string;
  phrase_fr?: string;
  ipa?: string;
};

export function cardSearchScore(card: SearchableCard, needle: string): number {
  const q = needle.trim().toLowerCase();
  if (!q) return 1;
  const lemma = (card.lemma || "").toLowerCase();
  const surface = (card.surface || "").toLowerCase();
  const french = (card.french || "").toLowerCase();
  const phrase = (card.phrase || "").toLowerCase();
  const phraseFr = (card.phrase_fr || "").toLowerCase();
  const ipa = (card.ipa || "").toLowerCase();
  if (lemma === q || surface === q) return 100;
  if (french === q) return 90;
  if (lemma.startsWith(q) || surface.startsWith(q)) return 80;
  if (french.startsWith(q)) return 70;
  if (lemma.includes(q) || surface.includes(q)) return 60;
  if (french.includes(q)) return 50;
  if (ipa.includes(q)) return 20;
  if (phrase.includes(q) || phraseFr.includes(q)) return 10;
  return 0;
}

export function filterAndRankCards<T extends SearchableCard>(cards: T[], query: string): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return cards;
  return cards
    .map((card, index) => ({ card, index, score: cardSearchScore(card, needle) }))
    .filter((row) => row.score > 0)
    .sort((a, b) => b.score - a.score || a.index - b.index)
    .map((row) => row.card);
}
