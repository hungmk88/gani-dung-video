import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { BPM_SAFE } from '../data/bpmSafe.ts';
import { bpmNotice, checkBpmSafe, complement, formatRanges } from './bpmSafety.ts';

const rules = JSON.parse(
  readFileSync(new URL('../../../config/rules.json', import.meta.url), 'utf-8'),
) as {
  qc: { duration_plan_gap_pct: number };
  music: {
    bpm_safe_domain: [number, number];
    bpm_safe_by_style_duration: Record<string, Record<string, { blocked: number[][]; warning: number[][] }>>;
  };
};

test('the copy in data/bpmSafe.ts is the table of config/rules.json', () => {
  assert.deepEqual(BPM_SAFE.domain, rules.music.bpm_safe_domain);
  assert.equal(BPM_SAFE.gapPct, Math.round(rules.qc.duration_plan_gap_pct * 100));
  assert.deepEqual(BPM_SAFE.byStyleDuration, rules.music.bpm_safe_by_style_duration);
});

test('levels of the known cases match the Python side', () => {
  const level = (style: string, d: number, bpm: number) => checkBpmSafe(BPM_SAFE, style, d, bpm).level;
  assert.equal(level('sang_trong', 15, 100), 'BLOCKING'); // 21.6 s planned for 15 s
  assert.equal(level('sang_trong', 15, 87), 'BLOCKING'); // inside the style's own 60–85 range
  assert.equal(level('sang_trong', 15, 88), 'WARNING'); // +1.36 s, cut
  assert.equal(level('ke_chuyen_nghe', 15, 80), 'OK');
  assert.equal(level('ke_chuyen_nghe', 15, 100), 'WARNING');
  assert.equal(level('tvc_cat_nhanh', 15, 120), 'OK');
  assert.equal(level('ban_hang_co_loi', 15, 100), 'OK'); // speech style: not in the table
});

test('outside the measured domain, and a decimal BPM, are handled', () => {
  assert.equal(checkBpmSafe(BPM_SAFE, 'sang_trong', 15, 30).level, 'OK');
  assert.equal(checkBpmSafe(BPM_SAFE, 'sang_trong', 15, 400).level, 'OK');
  assert.equal(checkBpmSafe(BPM_SAFE, 'sang_trong', 15, 100.4).level, 'BLOCKING');
});

test('buildable ranges are what blocked leaves free, inside the domain', () => {
  const { buildable } = checkBpmSafe(BPM_SAFE, 'sang_trong', 15, 100);
  const [lo, hi] = BPM_SAFE.domain;
  assert.deepEqual(buildable, complement(BPM_SAFE.byStyleDuration.sang_trong['15'].blocked, lo, hi));
  assert.ok(buildable.every(([a, b]) => !(a <= 100 && 100 <= b)));
});

test('complement and formatRanges', () => {
  assert.deepEqual(complement([[55, 65], [83, 87]], 55, 100), [[66, 82], [88, 100]]);
  assert.deepEqual(complement([], 55, 60), [[55, 60]]);
  assert.deepEqual(complement([[55, 60]], 55, 60), []);
  assert.equal(formatRanges([[66, 82], [90, 90]]), '66–82, 90');
});

test('the notice names the track, the style, the length and the way out', () => {
  const n = bpmNotice(BPM_SAFE, 'sang_trong', 'Sang trọng', 15, 100);
  assert.ok(n);
  assert.equal(n.level, 'BLOCKING');
  assert.equal(n.headline, 'Bài này ở BPM 100');
  assert.match(n.body, /"Sang trọng" và độ dài 15 giây/);
  assert.match(n.body, /66–82/);
  assert.match(n.body, /Chọn bài khác/);
});

test('a cut-only track gets the lighter notice and a fitting track none', () => {
  const cut = bpmNotice(BPM_SAFE, 'sang_trong', 'Sang trọng', 15, 88);
  assert.ok(cut);
  assert.equal(cut.level, 'WARNING');
  assert.match(cut.body, /cắt bớt/);
  assert.equal(bpmNotice(BPM_SAFE, 'ke_chuyen_nghe', 'Kể chuyện nghề', 15, 80), null);
});
