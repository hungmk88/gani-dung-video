import { Pause, Play, ShieldCheck, UploadCloud } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { BPM_SAFE } from '../../data/bpmSafe';
import { RULES } from '../../data/rules';
import { checkBpmSafe } from '../../lib/bpmSafety';
import { formatDuration } from '../../lib/format';
import { bpmLabel, musicView } from '../../lib/music';
import {
  MUSIC_MODE_IDS,
  type MusicListInfo,
  type MusicTrack,
  type VideoOptions,
} from '../../types/rules';

export function MusicPicker({
  options,
  onChange,
  list,
  error,
}: {
  options: VideoOptions;
  onChange: (patch: Partial<VideoOptions>) => void;
  /** The machine's track list (null until the first answer). */
  list: MusicListInfo | null;
  error: string | null;
}) {
  const [playingId, setPlayingId] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    return () => audioRef.current?.pause();
  }, []);

  function togglePlay(track: MusicTrack) {
    if (playingId === track.id) {
      audioRef.current?.pause();
      setPlayingId(null);
      return;
    }
    audioRef.current?.pause();
    const audio = new Audio(track.audio_url ?? track.file);
    audio.volume = 0.6;
    audio.onended = () => setPlayingId(null);
    void audio.play().catch(() => setPlayingId(null));
    audioRef.current = audio;
    setPlayingId(track.id);
  }

  const needsRights = RULES.musicModes[options.nhac as keyof typeof RULES.musicModes]?.needs_rights_tick;
  const view = musicView(list, error);
  const showList = options.nhac === 'thu_vien' || options.nhac === 'ai_tu_chon';

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {MUSIC_MODE_IDS.map((id) => {
          const m = RULES.musicModes[id];
          const selected = options.nhac === id;
          return (
            <button
              key={id}
              type="button"
              onClick={() => onChange({ nhac: id, nhac_quyen_su_dung: id === 'tai_len' ? false : null })}
              className={`rounded-lg border px-3 py-2 text-left text-xs font-medium transition ${
                selected
                  ? 'border-amber-500 bg-amber-500/10 text-amber-100'
                  : 'border-slate-800 bg-slate-900/40 text-slate-300 hover:border-slate-600'
              }`}
            >
              {m.name}
              {m.needs_rights_tick && <span className="ml-1 text-amber-400">•</span>}
            </button>
          );
        })}
      </div>

      {options.nhac === 'tai_len' && (
        <div className="flex items-center gap-3 rounded-lg border border-dashed border-slate-700 bg-slate-900/30 px-4 py-3 text-xs text-slate-400">
          <UploadCloud className="h-4 w-4 shrink-0" />
          Tải file nhạc của bạn (.mp3/.m4a/.wav/.aac/.flac/.ogg, tối đa 240s) — BPM sẽ được đo tự
          động (không dùng librosa, đo bằng numpy trên máy chủ thật ở GĐ6).
        </div>
      )}

      {showList && list !== null && error !== null && (
        <p role="alert" className="text-[11px] text-amber-400">
          Không làm mới được danh sách nhạc ({error}); đang hiện bản cũ, sẽ thử lại.
        </p>
      )}
      {showList && view === 'loading' && (
        <p className="rounded-lg border border-slate-800 bg-slate-900/30 px-4 py-3 text-xs text-slate-500">
          Đang lấy danh sách nhạc của máy dựng…
        </p>
      )}
      {showList && view === 'error' && (
        <p role="alert" className="rounded-lg border border-rose-800/50 bg-rose-950/30 px-4 py-3 text-xs text-rose-300">
          Không lấy được danh sách nhạc: {error}
        </p>
      )}
      {showList && view === 'not_configured' && (
        <p className="rounded-lg border border-dashed border-slate-700 bg-slate-900/30 px-4 py-3 text-xs text-slate-400">
          Máy dựng chưa có thư mục nhạc. Đặt <code className="font-mono-studio">GANI_DV_MUSIC_DIR</code>{' '}
          (đường dẫn thư mục chứa nhạc, nên kèm <code className="font-mono-studio">library.json</code>) trong
          .env của máy dựng rồi khởi động lại lệnh <code className="font-mono-studio">serve</code>.
        </p>
      )}
      {showList && view === 'empty' && (
        <p className="rounded-lg border border-dashed border-slate-700 bg-slate-900/30 px-4 py-3 text-xs text-slate-400">
          Thư mục nhạc đang trống. Chép file .mp3 .m4a .wav .aac .flac .ogg vào thư mục (không có
          thư mục con), làm mới trang là thấy.
        </p>
      )}

      {showList && view === 'ready' && list !== null && (
        <>
          {list.pending > 0 && (
            <p className="text-[11px] text-slate-500">
              Đang đo nhịp {list.pending} bài; kết quả hiện dần, không cần làm mới.
            </p>
          )}
          <ul className="divide-y divide-slate-800 overflow-hidden rounded-lg border border-slate-800">
            {list.tracks.map((track) => {
              const range = options.phong_cach ? RULES.styles[options.phong_cach].bpm_range : undefined;
              const level =
                track.bpm !== null && options.phong_cach && options.do_dai_giay
                  ? checkBpmSafe(BPM_SAFE, options.phong_cach, options.do_dai_giay, track.bpm).level
                  : 'OK';
              const inStyleRange =
                track.bpm !== null && range && track.bpm >= range[0] && track.bpm <= range[1];
              return (
                <li
                  key={track.id}
                  className={`flex items-center gap-3 bg-slate-900/40 px-3 py-2.5 ${
                    options.nhac_file === track.file ? 'bg-amber-500/5' : ''
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => togglePlay(track)}
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-slate-700 bg-slate-950 text-slate-300 hover:border-amber-500 hover:text-amber-300"
                    aria-label={playingId === track.id ? 'Tạm dừng' : 'Nghe thử'}
                  >
                    {playingId === track.id ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                  </button>
                  <button
                    type="button"
                    onClick={() => onChange({ nhac_file: track.file, nhac_bpm: track.bpm, bpm_override: null })}
                    className="flex-1 text-left"
                  >
                    <div className="text-sm text-slate-200">{track.title}</div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-slate-500">
                      <span className={inStyleRange && level !== 'BLOCKING' ? 'text-emerald-400' : ''}>
                        {bpmLabel(track)}
                      </span>
                      {track.bpm_source === 'library' && <span title="BPM do người duyệt khai báo">đã duyệt</span>}
                      {level === 'BLOCKING' && (
                        <span
                          className="rounded bg-rose-500/15 px-1.5 text-rose-300"
                          title="Kế hoạch lệch quá ±10%, video bị chặn khi giao"
                        >
                          không dựng được
                        </span>
                      )}
                      {level === 'WARNING' && (
                        <span
                          className="rounded bg-amber-500/15 px-1.5 text-amber-300"
                          title="Phần cuối video bị cắt bớt cho đúng độ dài"
                        >
                          cắt đuôi
                        </span>
                      )}
                      {track.duration_sec !== null && (
                        <>
                          <span>·</span>
                          <span>{formatDuration(track.duration_sec)}</span>
                        </>
                      )}
                      {track.genre !== '' && (
                        <>
                          <span>·</span>
                          <span>{track.genre}</span>
                        </>
                      )}
                      <span>·</span>
                      <span className="capitalize">{track.rights.replaceAll('_', ' ')}</span>
                    </div>
                  </button>
                  {options.nhac_file === track.file && (
                    <span className="shrink-0 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-300">
                      Đã chọn
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}

      {needsRights && (
        <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-slate-700 bg-slate-900/40 px-3 py-2.5 text-xs">
          <input
            type="checkbox"
            checked={options.nhac_quyen_su_dung ?? false}
            onChange={(e) => onChange({ nhac_quyen_su_dung: e.target.checked })}
            className="mt-0.5 h-3.5 w-3.5 accent-amber-500"
          />
          <span className="text-slate-300">
            <ShieldCheck className="mr-1 inline h-3.5 w-3.5 text-amber-400" />
            Tôi xác nhận có quyền sử dụng âm thanh này cho mục đích thương mại (X14)
          </span>
        </label>
      )}
    </div>
  );
}
