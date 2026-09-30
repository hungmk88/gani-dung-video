import { useEffect, useState } from 'react';
import { isOptionsComplete } from '../../lib/preflight';
import { api } from '../../services/api';
import type { FootageFile, PreflightResult, VideoOptions } from '../../types/rules';
import { FootageDropzone } from './FootageDropzone';
import { MusicPicker } from './MusicPicker';
import { PreflightValidator } from './PreflightValidator';
import { ProjectPicker, type RealJobChoice } from './ProjectPicker';
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

const DEFAULT_CHOICE: RealJobChoice = {
  mode: 'pipeline',
  projectId: null,
  draft: 'all',
  final: false,
  noAsr: false,
  forceAnalyze: false,
};
const DRAFT_LETTERS = ['A', 'B', 'C'] as const;
const PREFLIGHT_DEBOUNCE_MS = 250;

function messageOf(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function StudioWizard({ onJobCreated }: { onJobCreated: (jobId: string) => void }) {
  const isReal = api.mode === 'real';
  const [options, setOptions] = useState<VideoOptions>(DEFAULT_OPTIONS);
  const [footage, setFootage] = useState<FootageFile[]>([]);
  const [choice, setChoice] = useState<RealJobChoice>(DEFAULT_CHOICE);
  const [preflight, setPreflight] = useState<PreflightResult | null>(null);
  const [preflightError, setPreflightError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [longVideo, setLongVideo] = useState<boolean | null>(null);

  function patch(p: Partial<VideoOptions>) {
    setOptions((prev) => ({ ...prev, ...p }));
  }

  const incomplete = !isOptionsComplete(options);
  // "Chỉ dựng lại": the server takes everything from plan.json, so the matrix is not sent.
  const renderOnly = isReal && choice.mode === 'render';
  const draftLetters = DRAFT_LETTERS.slice(0, options.so_ban_nhap);

  // Whether the server has opened 3-minute videos (phase 7g); unknown until it answers.
  useEffect(() => {
    let cancelled = false;
    api
      .health()
      .then((h) => {
        if (!cancelled) setLongVideo(h.longVideo);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  // Debounced preflight through the API; `cancelled` drops answers of superseded inputs.
  useEffect(() => {
    if (incomplete || renderOnly) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      setChecking(true);
      api
        .preflight(options, footage)
        .then((r) => {
          if (cancelled) return;
          setPreflight(r);
          setPreflightError(null);
        })
        .catch((e: unknown) => {
          if (!cancelled) setPreflightError(messageOf(e));
        })
        .finally(() => {
          if (!cancelled) setChecking(false);
        });
    }, PREFLIGHT_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [options, footage, incomplete, renderOnly]);

  const needsProject = isReal && choice.projectId === null;

  async function handleSubmit() {
    setSubmitting(true);
    setSubmitError(null);
    try {
      const job = await api.submitJob({
        options,
        footage,
        projectId: choice.projectId ?? undefined,
        mode: choice.mode,
        // A letter the options do not produce (e.g. C with 2 drafts) would be a 400.
        draft:
          choice.mode === 'pipeline' && choice.draft !== 'all' && !draftLetters.includes(choice.draft)
            ? 'all'
            : choice.draft,
        final: choice.final,
        noAsr: choice.noAsr,
        forceAnalyze: choice.forceAnalyze,
      });
      onJobCreated(job.id);
    } catch (e) {
      setSubmitError(messageOf(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto grid max-w-7xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[1fr_380px]">
      <div className="space-y-6">
        {isReal ? (
          <Panel title="1. Chọn dự án trên máy dựng">
            <ProjectPicker
              value={choice}
              draftLetters={draftLetters}
              onChange={(p) => setChoice((c) => ({ ...c, ...p }))}
            />
          </Panel>
        ) : (
          <Panel title="1. Quản lý footage đầu vào">
            <FootageDropzone files={footage} onChange={setFootage} />
          </Panel>
        )}

        {!renderOnly && (
          <>
            <Panel title="2. Ma trận cấu hình kịch bản">
              <RuleSelector options={options} onChange={patch} longVideo={longVideo} />
            </Panel>

            <Panel title="3. Thư viện âm nhạc & bản quyền (X14)">
              <MusicPicker options={options} onChange={patch} />
            </Panel>
          </>
        )}
      </div>

      <div className="lg:sticky lg:top-24 lg:self-start">
        <PreflightValidator
          result={incomplete ? null : preflight}
          incomplete={incomplete}
          renderOnly={renderOnly}
          checking={checking}
          error={preflightError ?? submitError}
          missingProject={needsProject}
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
