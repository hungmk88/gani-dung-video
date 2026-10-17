// Run with `npm test`. Phase 6f: labels for the T8 (Whisper) status of a job's QA.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { ApiQa } from '../types/api.ts';
import { t8Note, toQaReport } from './mappers.ts';

const TEXT_GROUP = 'Chữ & phụ đề (T6, T7, T8)';

function qa(over: Partial<ApiQa> = {}): ApiQa {
  return {
    passed: true,
    rounds: 1,
    fixes: [],
    loudness_two_pass: false,
    lufs: -13.9,
    true_peak_db: -2,
    checks: [
      { code: 'T1', label: 'Khung', passed: true, detail: 'Đạt.' },
      { code: 'T6', label: 'Chữ chồng nhau', passed: true, detail: 'Đạt.' },
      { code: 'T7', label: 'Chữ quá dài', passed: true, detail: 'Đạt.' },
      { code: 'T3', label: 'Âm thanh', passed: true, detail: 'ok' },
    ],
    ...over,
  };
}

const group = (report: ReturnType<typeof toQaReport>) => report.groups.find((g) => g.title === TEXT_GROUP);

test('t8Note maps each status to the wording the owner chose', () => {
  assert.equal(t8Note('skipped_draft'), 'T8: không áp dụng cho bản nháp');
  assert.equal(t8Note('skipped_music'), 'T8: không áp dụng cho chế độ nhạc');
  assert.equal(t8Note('skipped_no_asr'), 'T8: đã tắt kiểm tra lời');
  assert.equal(t8Note('unavailable'), 'T8: chưa có số đo (bản dựng cũ hoặc thiếu Whisper)');
});

test('t8Note says nothing when T8 ran (its row carries the numbers) or the server is older', () => {
  assert.equal(t8Note('ran'), undefined);
  assert.equal(t8Note(undefined), undefined);
});

test('t8Note treats an unknown status as unavailable instead of hiding it', () => {
  assert.equal(t8Note('something_new'), t8Note('unavailable'));
});

test('a skipped T8 shows its note under the text group, without inventing a row', () => {
  const g = group(toQaReport(qa({ t8: 'skipped_music' })));
  assert.equal(g?.note, 'T8: không áp dụng cho chế độ nhạc');
  assert.deepEqual(g?.checks.map((c) => c.code), ['T6', 'T7']);
});

test('when T8 ran the group has the T8 row and no note', () => {
  const withRow = qa({
    t8: 'ran',
    checks: [
      ...qa().checks,
      { code: 'T8', label: 'Whisper nghe lại thành phẩm', passed: true, detail: 'Đạt: sai 5,5% số từ (tối đa 15%).' },
    ],
  });
  const g = group(toQaReport(withRow));
  assert.equal(g?.note, undefined);
  assert.deepEqual(g?.checks.map((c) => c.code), ['T6', 'T7', 'T8']);
});

test('a server without qa.t8 (before phase 6f) gets no T8 note', () => {
  assert.equal(group(toQaReport(qa()))?.note, undefined);
});

test('the note survives a text group that has no T6/T7 rows', () => {
  const g = group(toQaReport(qa({ t8: 'unavailable', checks: [{ code: 'T1', label: 'K', passed: true, detail: '' }] })));
  assert.equal(g?.note, 'T8: chưa có số đo (bản dựng cũ hoặc thiếu Whisper)');
  assert.equal(g?.checks.length, 0);
});

test('T8 labels never change the overall verdict', () => {
  assert.equal(toQaReport(qa({ t8: 'skipped_draft' })).overall_pass, true);
  assert.equal(toQaReport(qa({ passed: false, t8: 'skipped_draft' })).overall_pass, false);
});

test('platform risk words sit with the text checks, as a passed row with a warning', () => {
  const report = toQaReport(
    qa({
      checks: [
        ...qa().checks,
        { code: 'E1-NEN-TANG', label: 'Từ ngữ rủi ro theo nền tảng', passed: true, detail: 'Cảnh báo: zalo' },
      ],
    }),
  );
  const row = group(report)?.checks.find((c) => c.code === 'E1-NEN-TANG');
  assert.ok(row?.passed);
  assert.equal(report.groups.some((g) => g.title === 'Khác'), false);
});
