import { useMemo, useState } from 'react';
import { isOptionsComplete, runPreflight } from '../../lib/preflight';
import { api } from '../../services/api';
import type { FootageFile, VideoOptions } from '../../types/rules';
import { FootageDropzone } from './FootageDropzone';
import { MusicPicker } from './MusicPicker';
import { PreflightValidator } from './PreflightValidator';
import { RuleSelector } from './RuleSelector';

const DEFAULT_OPTIONS: VideoOptions = {
  phong_cach: '',
  muc_dich: '',
  do_dai_giay: 0,
  so_ban_nhap: 1,
  nhac: '',
  nhac_file: null,
  nhac_quyen_su_dung: null,
  khuyen_mai: null,
  ghi_chu: '',
  pacing: 'nhac',
};

export function StudioWizard({ onJobCreated }: { onJobCreated: (jobId: string) => void }) {
  const [options, setOptions] = useState<VideoOptions>(DEFAULT_OPTIONS);
  const [footage, setFootage] = useState<FootageFile[]>([]);
  const [submitting, setSubmitting] = useState(false);

  function patch(p: Partial<VideoOptions>) {
    setOptions((prev) => ({ ...prev, ...p }));
  }

  const preflight = useMemo(() => runPreflight({ options, footage }), [options, footage]);
  const incomplete = !isOptionsComplete(options);

  async function handleSubmit() {
    setSubmitting(true);
    try {
      const job = await api.submitJob(options, footage);
      onJobCreated(job.id);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto grid max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[1fr_380px]">
      <div className="space-y-6">
        <Panel title="1. Quản lý footage đầu vào">
          <FootageDropzone files={footage} onChange={setFootage} />
        </Panel>

        <Panel title="2. Ma trận cấu hình kịch bản">
          <RuleSelector options={options} onChange={patch} />
        </Panel>

        <Panel title="3. Thư viện âm nhạc & bản quyền (X14)">
          <MusicPicker options={options} onChange={patch} />
        </Panel>
      </div>

      <div className="lg:sticky lg:top-24 lg:self-start">
        <PreflightValidator
          result={preflight}
          incomplete={incomplete}
          submitting={submitting}
          onSubmit={handleSubmit}
        />
      </div>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-slate-800 bg-slate-900/30 p-4 sm:p-5">
      <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-400">{title}</h2>
      {children}
    </section>
  );
}
