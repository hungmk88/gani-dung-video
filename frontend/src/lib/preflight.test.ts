// Run with `npm test`. The browser preflight must say what domain/preflight.py says.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { RULES } from '../data/rules.ts';
import type { VideoOptions } from '../types/rules.ts';
import { runPreflight } from './preflight.ts';

function opts(over: Partial<VideoOptions>): VideoOptions {
  return {
    phong_cach: 'tvc_cat_nhanh',
    muc_dich: 'ban_hang',
    do_dai_giay: 30,
    so_ban_nhap: 1,
    nhac: 'thu_vien',
    nhac_file: 'a.m4a',
    nhac_quyen_su_dung: false,
    khuyen_mai: { uu_dai: '', thoi_han: '', dieu_kien: '' },
    ghi_chu: '',
    pacing: 'nhac',
    ...over,
  };
}

type StyleId = keyof typeof RULES.compatStyleDuration;

const codes = (o: VideoOptions) => runPreflight({ options: o, footage: [] }).warnings.map((w) => w.code);

test('X10 replaces the generic duration hint, as on the server', () => {
  for (const style of ['sang_trong', 'ke_chuyen_nghe'] as const) {
    assert.equal(RULES.compatStyleDuration[style]['15'], 'warn'); // the cell X10 covers
    const got = codes(opts({ phong_cach: style, muc_dich: 'gioi_thieu_xuong', do_dai_giay: 15 }));
    assert.ok(got.includes('X10'), `${style}: ${got.join(', ')}`);
    assert.ok(!got.includes('W_STYLE_DURATION'), `${style}: ${got.join(', ')}`);
  }
});

test('a "warn" duration cell outside X10 still gets the generic hint', () => {
  const cells = (Object.entries(RULES.compatStyleDuration) as [StyleId, Record<string, string>][]).flatMap(
    ([style, row]) =>
      Object.entries(row)
        .filter(([dur, v]) => v === 'warn' && !(dur === '15' && ['sang_trong', 'ke_chuyen_nghe'].includes(style)))
        .map(([dur]) => [style, Number(dur) as VideoOptions['do_dai_giay']] as const),
  );
  assert.ok(cells.length > 0);
  const [style, dur] = cells[0];
  const got = codes(opts({ phong_cach: style, do_dai_giay: dur }));
  assert.ok(got.includes('W_STYLE_DURATION') && !got.includes('X10'), got.join(', '));
});
