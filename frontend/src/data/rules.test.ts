import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { PRODUCT_NAMES } from './products.ts';
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

// ───────────────────────── the rest of what the demo shows (rules.json) ─────────────────────────
// Only what a person sees in the demo is compared: the names of styles and purposes, the warning
// texts, the durations and the product catalogue. Internal keys of rules.json (queue, render,
// music cache, BPM table, ...) are not copied on purpose: the browser has no use for them.

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const rules = JSON.parse(
  readFileSync(new URL('../../../config/rules.json', import.meta.url), 'utf-8'),
) as Json;

test('the rules version is the backend one', () => {
  assert.equal(RULES.rules_version, String(rules.rules_version));
});

test('the demo has the same styles, with the same display names and numbers', () => {
  assert.deepEqual(Object.keys(RULES.styles), Object.keys(rules.styles));
  for (const [id, s] of Object.entries<Json>(rules.styles)) {
    const d = RULES.styles[id as keyof typeof RULES.styles] as unknown as Json;
    assert.equal(d.id, id, `${id}: id`);
    assert.equal(d.name, s.name, `${id}: name`);
    assert.equal(d.driver, s.driver, `${id}: driver`);
    assert.equal(d.driver_fallback, s.driver_fallback, `${id}: driver_fallback`);
    assert.equal(d.driver_if_speech, s.driver_if_speech, `${id}: driver_if_speech`);
    assert.equal(d.scene_avg_sec, s.scene_avg_sec, `${id}: scene_avg_sec`);
    assert.deepEqual(d.bpm_range, s.bpm_range, `${id}: bpm_range`);
    assert.equal(d.max_duration, s.max_duration, `${id}: max_duration`);
    assert.equal(d.max_effects, s.max_effects, `${id}: max_effects`);
    assert.equal(d.grade, s.grade, `${id}: grade`);
    assert.equal(d.text_max_words, s.text?.max_words, `${id}: text_max_words`);
    assert.equal(d.requires_speech, s.requires_speech, `${id}: requires_speech`);
    assert.equal(d.note_min_words, s.note_min_words, `${id}: note_min_words`);
  }
});

test('the demo has the same purposes: name, call to action, blocks, longest length', () => {
  assert.deepEqual(Object.keys(RULES.purposes), Object.keys(rules.purposes));
  for (const [id, p] of Object.entries<Json>(rules.purposes)) {
    const d = RULES.purposes[id as keyof typeof RULES.purposes] as unknown as Json;
    assert.equal(d.id, id, `${id}: id`);
    assert.equal(d.name, p.name, `${id}: name`);
    assert.equal(d.cta, p.cta, `${id}: cta`);
    assert.deepEqual(d.blocks, p.blocks, `${id}: blocks`);
    assert.equal(d.max_duration, p.max_duration, `${id}: max_duration`);
    assert.deepEqual(d.required_fields, p.required_fields, `${id}: required_fields`);
  }
});

test('the lengths offered are the keys of durations in rules.json', () => {
  assert.deepEqual([...RULES.durations], Object.keys(rules.durations).map(Number));
});

test('the music modes are the backend ones: same order, names and rights tick', () => {
  const want = Object.entries<Json>(rules.music_modes).map(([id, m]) => ({
    id,
    name: m.name,
    needs_rights_tick: m.needs_rights_tick,
  }));
  assert.deepEqual(Object.values(RULES.musicModes).map((m) => ({ ...m })), want);
  assert.deepEqual(Object.keys(RULES.musicModes), Object.keys(rules.music_modes));
});

test('the draft counts offered are drafts.allowed', () => {
  assert.deepEqual([...RULES.draftCounts], rules.drafts.allowed);
});

test('the style × purpose and style × length matrices are the backend ones', () => {
  assert.deepEqual(RULES.compatStylePurpose, rules.compat_style_purpose);
  assert.deepEqual(RULES.compatStyleDuration, rules.compat_style_duration);
});

test('the scenes needed per style and length are the backend table (5.3.A)', () => {
  assert.deepEqual(RULES.scenesNeeded, rules.scenes_needed);
});

test('the shot names are pick.tag_names', () => {
  assert.deepEqual(RULES.pickTagNames, rules.pick.tag_names);
  assert.deepEqual(Object.keys(RULES.pickTagNames), Object.keys(rules.pick.tag_names));
});

test('the usable-footage ratio and the QC rounds are the backend numbers', () => {
  assert.equal(RULES.minUsableRatio, rules.footage.min_usable_ratio);
  assert.equal(RULES.qcMaxRounds, rules.qc.max_rounds);
});

// lib/preflight.ts does not read these rules from RULES: it hard-codes the conditions (X3, X10,
// X14, X15). These tests pin the rules.json values the code was written against; when one fails,
// update lib/preflight.ts together with the expected value here.
test('X10 fires for Sang trọng and Kể chuyện nghề at 15 s, as lib/preflight.ts assumes', () => {
  assert.deepEqual(rules.conflicts.X10.when, { styles: ['sang_trong', 'ke_chuyen_nghe'], duration: 15 });
});

test('X15 fires for 3 drafts at 180 s, as lib/preflight.ts assumes', () => {
  assert.deepEqual(rules.conflicts.X15.when, { drafts: 3, duration: 180 });
});

test('only Khuyến mãi has required fields (offer + deadline), as lib/preflight.ts assumes (X3)', () => {
  const withFields = Object.entries<Json>(rules.purposes)
    .filter(([, p]) => p.required_fields !== undefined)
    .map(([id, p]) => [id, p.required_fields]);
  assert.deepEqual(withFields, [['khuyen_mai', ['uu_dai', 'thoi_han']]]);
});

test('only uploaded music needs the rights tick, as lib/preflight.ts assumes (X14)', () => {
  const needTick = Object.entries<Json>(rules.music_modes)
    .filter(([, m]) => m.needs_rights_tick === true)
    .map(([id]) => id);
  assert.deepEqual(needTick, ['tai_len']);
});

test('the platforms are the backend ones, same order and defaults', () => {
  const profiles = rules.platforms.profiles as Record<string, { name: string; short: string; zalo_on_end_card: boolean }>;
  const want = Object.entries(profiles).map(([id, p]) => ({
    id,
    name: p.name,
    short: p.short,
    zaloOnEndCard: p.zalo_on_end_card,
    default: (rules.platforms.default as string[]).includes(id),
  }));
  assert.deepEqual(RULES.platforms.map((p) => ({ ...p })), want);
});

test('the block names are the backend ones', () => {
  assert.deepEqual({ ...RULES.blockNames }, rules.block_names);
});

test('the product catalogue of the delivery names is the backend one', () => {
  assert.deepEqual([...RULES.deliveryProducts], rules.delivery.naming.products);
});

test('every delivery product has a display name, and no name is left for an unknown product', () => {
  assert.deepEqual(Object.keys(PRODUCT_NAMES).sort(), [...rules.delivery.naming.products].sort());
  for (const [id, name] of Object.entries(PRODUCT_NAMES)) {
    assert.ok(name.trim().length > 0, `${id}: empty name`);
  }
});

// The browser can only judge a part of X1–X18 before any footage or AI call (see the comment in
// data/rules.ts). These are the codes the demo does not carry, and why: it never raises them, so
// a person never sees them. A NEW backend code is neither here nor in RULES: the test fails and a
// person decides which of the two it is.
const NOT_IN_DEMO: Record<string, string> = {
  X8: 'needs the CLIP labels of real footage',
  X12: 'needs the BPM of a track the browser has not measured',
  X13: 'needs the length of a track the browser has not measured',
  X16: 'a check on the AI note, after the AI call',
  X17: 'decided at render time',
  X18: 'decided at render time',
};

// Demo wording that differs from the backend ON PURPOSE: the browser cannot scan footage, so it
// says "estimate" or "not scanned yet". `backend` pins the backend text this wording was written
// against: when rules.json changes it, the test fails and the wording is reviewed again.
const DEMO_WORDING: Record<string, { backend: { desc: string; msg: string }; demo: { desc: string; msg: string } }> = {
  X5: {
    backend: {
      desc: rules.conflicts.X5.desc,
      msg: 'Chỉ có {usable} cảnh dùng được, cần ít nhất {need}. Thiếu {short} cảnh — giảm độ dài, đổi phong cách chậm hơn, hoặc quay bổ sung.',
    },
    demo: {
      desc: rules.conflicts.X5.desc,
      msg: 'Chỉ có {usable} cảnh dùng được (ước tính), cần ít nhất {need}. Thiếu {short} cảnh — giảm độ dài, đổi phong cách chậm hơn, hoặc quay bổ sung.',
    },
  },
  X6: {
    backend: {
      desc: rules.conflicts.X6.desc,
      msg: 'Có {usable} cảnh, nên có {ideal} để AI có lựa chọn. Sẽ lấy nhiều đoạn từ cùng clip; chất lượng có thể giảm.',
    },
    demo: {
      desc: rules.conflicts.X6.desc,
      msg: 'Có {usable} cảnh (ước tính), nên có {ideal} để AI có lựa chọn. Sẽ lấy nhiều đoạn từ cùng clip; chất lượng có thể giảm.',
    },
  },
  X7: {
    backend: {
      desc: 'Bán hàng có lời × không phát hiện lời nói',
      msg: 'Không thấy lời nói trong footage. Đổi phong cách, hoặc tải voice-over lên.',
    },
    demo: {
      desc: 'Bán hàng có lời × chưa rõ có lời nói',
      msg: 'Chưa quét được lời nói trong footage vừa tải (cần chạy inventory thật để biết chắc). Nếu footage không có ai nói, đổi phong cách hoặc tải voice-over lên.',
    },
  },
};

test('every backend warning is either in the demo or on the list of those it never raises', () => {
  const demo = new Set(Object.keys(RULES.conflicts));
  const missing = Object.keys(rules.conflicts).filter((c) => !demo.has(c));
  assert.deepEqual(missing.sort(), Object.keys(NOT_IN_DEMO).sort());
  for (const code of Object.keys(RULES.conflicts)) {
    assert.ok(code in rules.conflicts, `${code} is in the demo but not in rules.json`);
  }
});

test('each warning of the demo has the backend level, stage and text', () => {
  for (const [code, d] of Object.entries(RULES.conflicts)) {
    const b = rules.conflicts[code] as Json;
    assert.equal(d.code, code, `${code}: code`);
    assert.equal(d.level, b.level, `${code}: level`);
    assert.equal(d.stage, b.stage, `${code}: stage`);
    const wording = DEMO_WORDING[code];
    if (wording === undefined) {
      assert.equal(d.desc, b.desc, `${code}: desc`);
      assert.equal(d.msg, b.msg, `${code}: msg`);
    } else {
      // the backend text the demo wording was written against has not moved ...
      assert.equal(b.desc, wording.backend.desc, `${code}: backend desc changed, re-read the demo wording`);
      assert.equal(b.msg, wording.backend.msg, `${code}: backend msg changed, re-read the demo wording`);
      // ... and the demo says exactly what was reviewed
      assert.equal(d.desc, wording.demo.desc, `${code}: demo desc`);
      assert.equal(d.msg, wording.demo.msg, `${code}: demo msg`);
    }
  }
});
