import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { RULES } from './rules.ts';

// The source of truth is the backend: config/glossary.json → banned_claims. RULES.bannedClaims is
// a copy used by the demo mode (services/mockApi.ts); this test fails when the two drift apart.
const glossary = JSON.parse(
  readFileSync(new URL('../../../config/glossary.json', import.meta.url), 'utf-8'),
) as { banned_claims: string[] };

test('the demo copy has as many banned phrases as config/glossary.json', () => {
  assert.equal(RULES.bannedClaims.length, glossary.banned_claims.length);
  // NVH 14/10/2026: 19 phrases + 4 for "100 phần trăm tự nhiên/thiên nhiên". The Python test
  // test_banned_list_is_the_agreed_phrase_list pins the same number: change both together.
  assert.equal(RULES.bannedClaims.length, 23);
});

test('every banned phrase of the demo copy is the glossary phrase, character for character', () => {
  assert.deepEqual(RULES.bannedClaims, glossary.banned_claims); // same text, same order, no folding
});
