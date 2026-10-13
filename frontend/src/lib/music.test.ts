import assert from 'node:assert/strict';
import test from 'node:test';
import type { MusicListInfo, MusicTrack } from '../types/rules.ts';
import { bpmLabel, musicBadge, musicView, shouldPoll } from './music.ts';

const track = (over: Partial<MusicTrack> = {}): MusicTrack => ({
  id: 'a.mp3', title: 'a', bpm: 92, duration_sec: 100, genre: '', rights: 'thu_vien_noi_bo',
  file: 'a.mp3', ...over,
});
const list = (over: Partial<MusicListInfo> = {}): MusicListInfo => ({
  configured: true, pending: 0, tracks: [track()], ...over,
});

test('the picker shows loading, error, instructions, empty or the list', () => {
  assert.equal(musicView(null, null), 'loading');
  assert.equal(musicView(null, 'mất kết nối'), 'error');
  assert.equal(musicView(list({ configured: false, tracks: [] }), null), 'not_configured');
  assert.equal(musicView(list({ tracks: [] }), null), 'empty');
  assert.equal(musicView(list(), null), 'ready');
});

test('an old list stays on screen when a later refresh fails', () => {
  assert.equal(musicView(list(), 'lỗi mạng'), 'ready');
});

test('it asks again only while tracks are pending', () => {
  assert.equal(shouldPoll(null), false);
  assert.equal(shouldPoll(list({ pending: 2 })), true);
  assert.equal(shouldPoll(list({ pending: 0 })), false);
  assert.equal(shouldPoll(list({ configured: false, pending: 3, tracks: [] })), false);
});

test('the BPM label says why there is no number', () => {
  assert.equal(bpmLabel(track()), '92 BPM');
  assert.equal(bpmLabel(track({ bpm: 88.01 })), '88 BPM');
  assert.equal(bpmLabel(track({ bpm: 92.35 })), '92.4 BPM');
  assert.equal(bpmLabel(track({ bpm: null, bpm_source: 'pending' })), 'đang đo nhịp');
  assert.equal(bpmLabel(track({ bpm: null, bpm_source: 'unknown' })), 'không đo được nhịp');
});

test('the header badge is honest about demo tracks and about the real folder', () => {
  assert.deepEqual(musicBadge('mock', list({ tracks: [track(), track({ id: 'b' })] })), {
    value: '2/24 bài (demo)', tone: 'amber',
  });
  assert.equal(musicBadge('real', null).tone, 'slate');
  assert.equal(musicBadge('real', list({ configured: false, tracks: [] })).value, 'chưa đặt thư mục nhạc');
  assert.deepEqual(musicBadge('real', list()), { value: '1 bài', tone: 'emerald' });
  assert.equal(musicBadge('real', list({ pending: 2 })).value, '1 bài, đang đo 2');
  assert.equal(musicBadge('real', list({ tracks: [] })).tone, 'amber');
});
