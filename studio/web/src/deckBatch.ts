export type BatchDeck = { id: string; name: string };
export type BatchResult = { deck: BatchDeck; ok: boolean; error?: string; warnings?: string[] };

/** Keep imports/uploads serial to bound exports and avoid competing replacements. */
export async function runDeckBatch(
  decks: BatchDeck[],
  send: (id: string) => Promise<{ ok: boolean; warnings?: string[] }>,
  onProgress: (completed: number, total: number, deck: BatchDeck) => void,
  onResult: (result: BatchResult) => void,
  shouldStop: () => boolean = () => false,
) {
  const results: BatchResult[] = [];
  const unique = [...new Map(decks.map((deck) => [deck.id, deck])).values()];
  for (const deck of unique) {
    if (shouldStop()) break;
    onProgress(results.length, unique.length, deck);
    let result: BatchResult;
    try {
      const response = await send(deck.id);
      if (!response.ok) throw new Error("Operation failed");
      result = { deck, ok: true };
      if (response.warnings?.length) result.warnings = response.warnings;
    } catch (error) {
      result = { deck, ok: false, error: error instanceof Error ? error.message : String(error) };
    }
    results.push(result);
    onResult(result);
  }
  return results;
}
