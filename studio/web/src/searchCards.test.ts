import assert from "node:assert/strict";
import { test } from "node:test";
import { filterAndRankCards } from "./searchCards.ts";

test("searching school ranks the lemma first, not a phrase hit like to", () => {
  const cards = [
    { lemma: "to", surface: "to", french: "à, vers", phrase: "They went to the park after school." },
    { lemma: "she", surface: "she", french: "elle", phrase: "She goes to school by bus." },
    { lemma: "school", surface: "school", french: "école", phrase: "Students learn many things at school." },
    { lemma: "house", surface: "house", french: "maison", phrase: "The house is big." },
  ];
  const ranked = filterAndRankCards(cards, "school");
  assert.equal(ranked[0]?.lemma, "school");
  assert.ok(ranked.some((card) => card.lemma === "to"));
  assert.ok(!ranked.some((card) => card.lemma === "house"));
});
