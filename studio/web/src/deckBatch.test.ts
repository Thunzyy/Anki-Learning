import test from "node:test";
import assert from "node:assert/strict";
import { runDeckBatch } from "./deckBatch.ts";

test("all languages are sent serially, once per deck, and failures do not hide later successes", async () => {
  const calls: string[] = [];
  let active = false;
  const progress: number[] = [];
  const results = await runDeckBatch([
    { id: "pt", name: "Portuguese" }, { id: "es", name: "Spanish" },
    { id: "en", name: "English" }, { id: "pt", name: "Portuguese" },
  ], async (id) => {
    assert.equal(active, false);
    active = true;
    await new Promise((resolve) => setTimeout(resolve, 2));
    calls.push(id);
    active = false;
    if (id === "es") throw new Error("QA: missing example");
    return { ok: true };
  }, (completed, total) => { progress.push(completed); assert.equal(total, 3); }, () => {});
  assert.deepEqual(calls, ["pt", "es", "en"]);
  assert.deepEqual(progress, [0, 1, 2]);
  assert.deepEqual(results.map((result) => result.ok), [true, false, true]);
  assert.equal(results[1].error, "QA: missing example");
});

test("stop finishes the current import and leaves remaining decks untouched", async () => {
  let stopped = false;
  const results = await runDeckBatch([{ id: "a", name: "A" }, { id: "b", name: "B" }],
    async () => { stopped = true; return { ok: true }; }, () => {}, () => {}, () => stopped);
  assert.equal(results.length, 1);
  assert.equal(results[0].deck.id, "a");
});

test("an explicit rejection is not a success and retry can select only failed decks", async () => {
  const queue = [{ id: "a", name: "A" }, { id: "b", name: "B" }];
  const first = await runDeckBatch(queue, async (id) => ({ ok: id === "a" }), () => {}, () => {});
  const calls: string[] = [];
  const retry = await runDeckBatch(first.filter((result) => !result.ok).map((result) => result.deck),
    async (id) => { calls.push(id); return { ok: true }; }, () => {}, () => {});
  assert.deepEqual(calls, ["b"]);
  assert.equal(retry[0].ok, true);
});

test("empty or pre-stopped queue never imports", async () => {
  const send = async () => { assert.fail("Unexpected import"); return { ok: true }; };
  assert.deepEqual(await runDeckBatch([], send, () => {}, () => {}), []);
  assert.deepEqual(await runDeckBatch([{ id: "a", name: "A" }], send, () => {}, () => {}, () => true), []);
});

test("GitHub cleanup warnings survive a successful publication", async () => {
  const results = await runDeckBatch([{ id: "pt", name: "Portuguese" }],
    async () => ({ ok: true, warnings: ["Backup asset retained"] }), () => {}, () => {});
  assert.equal(results[0].ok, true);
  assert.deepEqual(results[0].warnings, ["Backup asset retained"]);
});
