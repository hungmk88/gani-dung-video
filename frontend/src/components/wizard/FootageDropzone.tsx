import { Clock, FileVideo, HardDrive, UploadCloud, X } from 'lucide-react';
import { useCallback, useRef, useState } from 'react';
import { formatBytes, formatDuration } from '../../lib/format';
import type { FootageFile } from '../../types/rules';

const VIDEO_EXTENSIONS = ['.mp4', '.mov', '.m4v', '.mkv', '.avi', '.webm', '.mts'];

// No real ffprobe in the browser — duration is read from the HTML5 <video> element
// (real number, not guessed) once metadata loads; falls back to 0 until then.
function probeDuration(file: File): Promise<number> {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(file);
    const video = document.createElement('video');
    video.preload = 'metadata';
    video.onloadedmetadata = () => {
      URL.revokeObjectURL(url);
      resolve(Number.isFinite(video.duration) ? video.duration : 0);
    };
    video.onerror = () => {
      URL.revokeObjectURL(url);
      resolve(0);
    };
    video.src = url;
  });
}

export function FootageDropzone({
  files,
  onChange,
}: {
  files: FootageFile[];
  onChange: (files: FootageFile[]) => void;
}) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const addFiles = useCallback(
    async (list: FileList | null) => {
      if (!list) return;
      const accepted = [...list].filter((f) =>
        VIDEO_EXTENSIONS.some((ext) => f.name.toLowerCase().endsWith(ext)),
      );
      const withDuration: FootageFile[] = await Promise.all(
        accepted.map(async (f) => ({
          id: `${f.name}-${f.size}-${f.lastModified}`,
          name: f.name,
          size_bytes: f.size,
          duration_sec: await probeDuration(f),
        })),
      );
      onChange([...files, ...withDuration].filter((f, i, arr) => arr.findIndex((x) => x.id === f.id) === i));
    },
    [files, onChange],
  );

  const totalSize = files.reduce((s, f) => s + f.size_bytes, 0);
  const totalDuration = files.reduce((s, f) => s + f.duration_sec, 0);

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          void addFiles(e.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-10 text-center transition ${
          dragOver
            ? 'border-amber-500 bg-amber-500/5'
            : 'border-slate-700 bg-slate-900/40 hover:border-slate-600'
        }`}
      >
        <UploadCloud className={`mb-3 h-8 w-8 ${dragOver ? 'text-amber-400' : 'text-slate-400'}`} />
        <p className="text-sm font-medium text-slate-200">Kéo thả file MP4 hoặc cả thư mục footage vào đây</p>
        <p className="mt-1 text-xs text-slate-400">
          Hỗ trợ {VIDEO_EXTENSIONS.join(', ')} — hoặc bấm để chọn file
        </p>
        <input
          ref={inputRef}
          type="file"
          accept="video/*"
          multiple
          // @ts-expect-error webkitdirectory is a non-standard but widely supported attribute
          webkitdirectory=""
          className="hidden"
          onChange={(e) => void addFiles(e.target.files)}
        />
      </div>

      <div className="grid grid-cols-3 gap-3">
        <StatCard icon={FileVideo} label="Số file" value={String(files.length)} />
        <StatCard icon={HardDrive} label="Tổng dung lượng" value={formatBytes(totalSize)} />
        <StatCard icon={Clock} label="Thời lượng thô" value={formatDuration(totalDuration)} />
      </div>

      {files.length > 0 && (
        <ul className="max-h-40 space-y-1 overflow-y-auto scroll-thin rounded-lg border border-slate-800 bg-slate-900/40 p-2">
          {files.map((f) => (
            <li
              key={f.id}
              className="flex items-center justify-between gap-2 rounded px-2 py-1 text-xs text-slate-300 hover:bg-slate-800/60"
            >
              <span className="truncate">{f.name}</span>
              <span className="flex shrink-0 items-center gap-2 text-slate-400">
                <span>{formatBytes(f.size_bytes)}</span>
                <span>{formatDuration(f.duration_sec)}</span>
                <button
                  type="button"
                  onClick={() => onChange(files.filter((x) => x.id !== f.id))}
                  className="rounded p-0.5 text-slate-400 hover:bg-slate-700 hover:text-rose-400"
                  aria-label={`Bỏ ${f.name}`}
                >
                  <X className="h-3 w-3" />
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function StatCard({ icon: Icon, label, value }: { icon: typeof Clock; label: string; value: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 px-3 py-2">
      <div className="flex items-center gap-1.5 text-xs text-slate-400">
        <Icon className="h-3 w-3" />
        {label}
      </div>
      <div className="mt-0.5 text-lg font-semibold text-slate-100">{value}</div>
    </div>
  );
}
