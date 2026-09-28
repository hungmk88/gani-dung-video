import type { MusicTrack } from '../types/rules';

/**
 * The 4 tracks below are REAL files, copied from `Nhac test BPM/` (repo root, one level
 * up) into `public/audio/`. They are synthetic click-tracks built with ffmpeg to test
 * `snap_to_beats()` (phase 4a) — not licensed music, so `rights: 'royalty_free'` is
 * literally true, not a placeholder. BPM/duration are the confirmed values from
 * `Nhac test BPM/DOC-TRUOC.txt` (autocorrelation-checked 28/09/2026), not guesses.
 *
 * The real GANI music library (target ≥ 24 tracks per docs/KE-HOACH.md phase 5 input)
 * does not exist yet — see the "Thư viện nhạc" badge in Header.tsx, which reports 4/24
 * honestly instead of padding this list with invented songs.
 */
export const MUSIC_LIBRARY: MusicTrack[] = [
  {
    id: 'test-72bpm',
    title: '72bpm — Sang trọng (click-track thử nghiệm)',
    bpm: 72,
    duration_sec: 200,
    genre: 'Test tone · nhịp 4/4',
    rights: 'royalty_free',
    file: '/audio/72bpm-sang-trong.m4a',
  },
  {
    id: 'test-84bpm',
    title: '84bpm — Kể chuyện nghề (click-track thử nghiệm)',
    bpm: 84,
    duration_sec: 200,
    genre: 'Test tone · nhịp 4/4',
    rights: 'royalty_free',
    file: '/audio/84bpm-ke-chuyen-nghe.m4a',
  },
  {
    id: 'test-108bpm',
    title: '108bpm — Review món ăn (click-track thử nghiệm)',
    bpm: 108,
    duration_sec: 200,
    genre: 'Test tone · nhịp 4/4',
    rights: 'royalty_free',
    file: '/audio/108bpm-review-mon-an.m4a',
  },
  {
    id: 'test-124bpm',
    title: '124bpm — TVC cắt nhanh (click-track thử nghiệm)',
    bpm: 124,
    duration_sec: 200,
    genre: 'Test tone · nhịp 4/4',
    rights: 'royalty_free',
    file: '/audio/124bpm-tvc-cat-nhanh.m4a',
  },
];
