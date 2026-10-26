"""Edit decision list (phase 5a): one checked draft → everything ffmpeg needs, in frames.

Pure code. Cut points come from snap_to_beats (phase 4a) and are never moved here: a
dissolve borrows extra frames from the END of the outgoing shot, so the incoming shot still
starts on its beat (xfade overlaps the two, it does not shift the timeline).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from gani_dung_video.domain.beats import SnappedSegment
from gani_dung_video.domain.drafts import SnappedDraft
from gani_dung_video.domain.finish import FinishError, denoise_filter, grade_filter
from gani_dung_video.domain.footage import Clip
from gani_dung_video.domain.karaoke import group_words
from gani_dung_video.domain.plan import Plan
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.speech import SpokenWord
from gani_dung_video.domain.speech_edit import Overlay, SpeechCut, duck_intervals
from gani_dung_video.domain.zoom import Room, Span, Zoom, beat_punches, speech_zooms, zoom_room


class EdlError(ValueError):
    """The draft cannot be rendered as is (message in Vietnamese)."""


class Shot(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str  # file name inside the footage folder
    src_in: float
    src_out: float  # includes the dissolve tail when there is one
    t_start: float  # timeline, on the beat
    t_end: float
    block: str
    hdr: bool
    dissolve_out_sec: float  # cross-fade into the next shot (0 = hard cut)
    pad_sec: float  # tail the source is too short for: freeze its last frame
    zoom: float = 1.0  # whole-shot framing (domain/zoom.py, 26/10/2026)
    punch_sec: float | None = None  # punch-in this many seconds into the shot
    punch_zoom: float = 1.0  # framing from punch_sec on


class MusicTrack(BaseModel):
    model_config = ConfigDict(frozen=True)

    track: str
    audio_in_sec: float  # start the file here so video time 0 is beat 0
    loop_sec: float | None  # whole-bar loop length when the track is shorter than the video
    fade_out_sec: float


class Caption(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: float
    end: float
    text: str
    kind: str  # text | cta | title (chapter title, top of the frame)


class Edl(BaseModel):
    style: str
    width: int
    height: int
    fps: int
    video_kbps: int
    shots: list[Shot]
    content_end: float
    end_card_sec: float
    duration: float
    music: MusicTrack | None
    ambient_db: float | None  # None = mute the camera sound
    lufs: float
    true_peak_db: float
    font: str
    captions: list[Caption]
    mode: str = "music"  # music | grid | speech
    overlays: list[Shot] = []  # speech: B-roll pictures laid over the speaker (no sound)
    duck: list[tuple[float, float]] = []  # speech: voiced spans where the music dips
    duck_db: float | None = None
    duck_fade_sec: float = 0.15
    karaoke: list[list[SpokenWord]] = []  # speech: word groups on the timeline
    denoise: str | None = None  # T-A: afftdn for the camera-sound layer; None = off
    grade: str | None = None  # T-D: eq/colorbalance for the picture; None = off
    emphasis: list[str] = []  # T-C: approved keywords to highlight in captions (set by the gate)
    trim_fade_sec: float = 0.0  # music mode cut at the chosen length: fade the last seconds
    platform: str = ""  # tiktok | facebook (domain/platforms.apply_platform); "" = no profile
    end_card_zalo: bool = True  # the end card shows the Zalo line (TikTok: no, 16/10/2026)
    no_music: bool = False  # the plan's music was left out: music is added in the app (16/10/2026)


def _finishing(r: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """T-A / T-D filter strings (denoise, grade) for the Edl, None = off.

    Raises:
        EdlError: a finishing setting in rules.json is out of range.
    """
    try:
        return denoise_filter(r), grade_filter(r)
    except FinishError as e:
        raise EdlError(str(e)) from e


class BrollPlacement(BaseModel):
    """Which candidate segment fills an overlay slot (5c-2: chosen by the AI)."""

    model_config = ConfigDict(frozen=True)

    overlay: Overlay
    clip: str
    start: float
    end: float
    peak: float


class SpeechDraft(BaseModel):
    """One speech-mode draft: chosen A-roll phrases, in order, plus optional B-roll overlays.

    5c-1: hand-assembled from phrases.json to test the render pipeline; 5c-2 will have
    Claude produce this shape instead.
    """

    draft: str
    label: str
    cuts: list[SpeechCut]
    broll: list[BrollPlacement] = []
    missing_shots: list[str] = []
    warnings: list[str] = []


def _frame(t: float, fps: int) -> float:
    return round(t * fps) / fps


def _trim_draft(draft: SnappedDraft, to_sec: float, fade_sec: float, fps: int) -> SnappedDraft:
    """Cut a draft that runs past `to_sec` (music mode, plan.trim_to_sec).

    Cuts after `to_sec` are dropped and the cut that crosses it is shortened (the source keeps its
    in point). A closing fragment shorter than the fade could not be seen, so it is absorbed by
    the shot before it.
    """
    half = 0.5 / fps
    kept: list[SnappedSegment] = []
    for seg in draft.segments:
        if seg.t_start >= to_sec - half:
            break
        kept.append(seg)
    if not kept:
        return draft
    if len(kept) > 1 and to_sec - kept[-1].t_start < fade_sec:
        kept.pop()
    last = kept[-1]
    kept[-1] = last.model_copy(
        update={"t_end": to_sec, "src_out": last.src_in + to_sec - last.t_start}
    )
    return draft.model_copy(update={"segments": kept})


def _dissolves(segs: Sequence[Any], style: Mapping[str, Any], fps: int,
               group_of: Mapping[str, str] | None = None) -> list[float]:  # fmt: skip
    """Seconds of cross-fade out of each shot: everywhere, only between blocks, or never.

    `group_of` maps a block to its chapter (3-minute videos): the sub-blocks inside a chapter are
    one block as far as the style's "between blocks" rule goes."""
    n = len(segs)
    if style.get("transition") == "dissolve_all":
        d = int(style.get("dissolve_frames", 0)) / fps
        return [d if i < n - 1 else 0.0 for i in range(n)]
    between = int(style.get("dissolve_between_blocks", 0)) / fps
    group = group_of or {}

    def key(i: int) -> str:
        return group.get(segs[i].block, segs[i].block)

    return [between if i < n - 1 and key(i) != key(i + 1) else 0.0 for i in range(n)]


def _shots(draft: SnappedDraft, style: Mapping[str, Any], clips: Mapping[str, Clip], fps: int,
           group_of: Mapping[str, str] | None = None) -> list[Shot]:  # fmt: skip
    """Shots in whole frames. The source window is the cut's own length (+ its dissolve) counted
    from the rounded in-point, so source + freeze - dissolve is exactly t_end - t_start frames
    and the joined picture (concat/xfade only add lengths up) lands on every cut. A source too
    short for that ends on its last whole frame; the rest is frozen (pad)."""
    segs = draft.segments
    out: list[Shot] = []
    for seg, d in zip(segs, _dissolves(segs, style, fps, group_of), strict=True):
        source = _source(seg, clips)
        clip = clips[source]
        f_in, f0, f1, fd = (round(x * fps) for x in (seg.src_in, seg.t_start, seg.t_end, d))
        want = f_in + (f1 - f0) + fd
        f_out = max(f_in, min(want, math.floor(clip.duration_sec * fps + 1e-3)))
        out.append(Shot(source=source, src_in=f_in / fps, src_out=f_out / fps, t_start=f0 / fps,
                        t_end=f1 / fps, block=seg.block, hdr=clip.is_hdr,
                        dissolve_out_sec=fd / fps, pad_sec=(want - f_out) / fps))  # fmt: skip
    return out


def _source(seg: Any, clips: Mapping[str, Clip]) -> str:
    """The segment's clip file, which must be in the inventory."""
    if seg.clip not in clips:
        raise EdlError(f"Cảnh '{seg.scene_id}' dùng clip '{seg.clip or '?'}' không có trong "
                       "inventory.json — chạy lại lệnh `inventory` rồi `draft`.")  # fmt: skip
    return str(seg.clip)


def _captions(draft: SnappedDraft, plan: Plan, fps: int) -> list[Caption]:
    """One line per block stays for the whole block; otherwise a line pops for its shot."""
    cta = plan.blocks[-1].role
    per_block = "per_block" in plan.text_rules
    out: list[Caption] = _chapter_titles(draft, plan, fps)
    for seg in draft.segments:
        if not seg.text.strip():
            continue
        end = seg.t_end
        if per_block or seg.block == cta:
            end = max(s.t_end for s in draft.segments if s.block == seg.block)
        kind = "cta" if seg.block == cta else "text"
        out.append(Caption(start=_frame(seg.t_start, fps), end=_frame(end, fps),
                           text=seg.text.strip(), kind=kind))  # fmt: skip
    return sorted(out, key=lambda c: c.start)


def _chapter_titles(draft: SnappedDraft, plan: Plan, fps: int) -> list[Caption]:
    """3-minute videos: each chapter's title over its first `title_beats` beats (no time added),
    never past the end of the chapter."""
    title_of = {t.chapter: t.title.strip() for t in draft.chapter_titles}
    out: list[Caption] = []
    for chapter in plan.chapters:
        mine = [s for s in draft.segments if s.block in chapter.blocks]
        text = title_of.get(chapter.id, "")
        if not mine or not text:
            continue
        start = mine[0].t_start
        end = min(start + chapter.title_beats * plan.beat_sec, mine[-1].t_end)
        out.append(Caption(start=_frame(start, fps), end=_frame(end, fps), text=text,
                           kind="title"))  # fmt: skip
    return out


def build_edl(
    draft: SnappedDraft, plan: Plan, clips: Mapping[str, Clip], rules: Rules, preview: bool
) -> Edl:
    """Turn one draft into an EDL.

    Raises:
        EdlError: a segment refers to a clip that is not in the inventory.
    """
    r = rules["render"]
    style_name = str(plan.options["style"])
    style = rules["styles"][style_name]
    fps = int(r["fps"])
    size = r["preview"] if preview else r["final"]
    group_of = {b.role: b.chapter for b in plan.blocks if b.chapter is not None}
    to = plan.trim_to_sec
    if to is not None and draft.segments and draft.segments[-1].t_end > to + 0.5 / fps:
        draft = _trim_draft(draft, to, plan.trim_fade_sec, fps)
    else:
        to = None
    shots = _shots(draft, style, clips, fps, group_of)
    room = _room(clips, rules)
    shots = _zoomed(shots, beat_punches(_spans(shots), style, plan.beat_sec, fps, room))
    content_end = shots[-1].t_end if shots else 0.0
    end_card = float(r["end_card_sec"])
    m = plan.music
    music = MusicTrack(track=m.track, audio_in_sec=m.audio_in_sec, loop_sec=m.loop_sec,
                       fade_out_sec=float(r["music_fade_sec"])) if m else None  # fmt: skip
    amb = style.get("ambient_db")
    denoise, grade = _finishing(r)
    return Edl(
        style=style_name, width=int(size["width"]), height=int(size["height"]), fps=fps,
        video_kbps=int(size["video_kbps"]), shots=shots, content_end=content_end,
        end_card_sec=end_card, duration=_frame(content_end + end_card, fps), music=music,
        ambient_db=float(amb) if amb is not None else None, lufs=float(r["lufs"]),
        true_peak_db=float(r["true_peak_db"]),
        font=str(r["fonts"].get(style_name, r["fonts"]["default"])),
        captions=_captions(draft, plan, fps), denoise=denoise, grade=grade,
        trim_fade_sec=plan.trim_fade_sec if to is not None else 0.0,
    )  # fmt: skip


def _spans(shots: Sequence[Shot]) -> list[Span]:
    return [Span(s.t_start, s.t_end, s.source) for s in shots]


def _room(clips: Mapping[str, Clip], rules: Rules) -> Room:
    final = rules["render"]["final"]
    return zoom_room(clips, int(final["width"]), int(final["height"]),
                     float(rules["effects"]["max_upscale"]))  # fmt: skip


def _zoomed(shots: list[Shot], zooms: Mapping[int, Zoom]) -> list[Shot]:
    return [s.model_copy(update={"zoom": z.base, "punch_sec": z.punch_sec, "punch_zoom": z.punch})
            if (z := zooms.get(i)) else s for i, s in enumerate(shots)]  # fmt: skip


def _broll_shot(b: BrollPlacement, clips: Mapping[str, Clip], fps: int) -> Shot:
    if b.clip not in clips:
        raise EdlError(f"B-roll dùng clip '{b.clip}' không có trong inventory.json.")
    d = b.overlay.t_end - b.overlay.t_start
    if b.end - b.start + 1e-6 < d:  # would show other footage, or the speaker past clip end
        raise EdlError(f"B-roll '{b.clip}' ({b.start:.2f}–{b.end:.2f}s) ngắn hơn ô {d:.2f}s của "
                       f"câu '{b.overlay.phrase_id}' — chạy lại lệnh `draft`.")  # fmt: skip
    src_in = min(max(b.peak - 0.35 * d, b.start), max(b.start, b.end - d))  # peak at 35 %
    return Shot(source=b.clip, src_in=_frame(src_in, fps), src_out=_frame(src_in + d, fps),
                t_start=_frame(b.overlay.t_start, fps), t_end=_frame(b.overlay.t_end, fps),
                block="broll", hdr=clips[b.clip].is_hdr, dissolve_out_sec=0.0,
                pad_sec=0.0)  # fmt: skip


def build_speech_edl(
    cuts: Sequence[SpeechCut], broll: Sequence[BrollPlacement], plan: Plan,
    clips: Mapping[str, Clip], rules: Rules, preview: bool,
) -> Edl:  # fmt: skip
    """Speech-driven EDL (5c-1): the speaker's phrases carry picture and sound, B-roll covers
    parts of them silently, music dips under the voice, words become karaoke subtitles.

    Raises:
        EdlError: a phrase or B-roll clip is not in the inventory.
    """
    r, fps = rules["render"], int(rules["render"]["fps"])
    style_name = str(plan.options["style"])
    style, se = rules["styles"][style_name], rules["speech_edit"]
    size = r["preview"] if preview else r["final"]
    shots = []
    for c in cuts:
        if c.clip not in clips:
            raise EdlError(f"Câu '{c.phrase_id}' dùng clip '{c.clip}' không có trong inventory.")
        shots.append(Shot(source=c.clip, src_in=c.src_in, src_out=c.src_out, t_start=c.t_start,
                          t_end=c.t_end, block="speech", hdr=clips[c.clip].is_hdr,
                          dissolve_out_sec=0.0, pad_sec=0.0))  # fmt: skip
    covered = [(b.overlay.t_start, b.overlay.t_end) for b in broll]
    zooms = speech_zooms(_spans(shots), [c.words for c in cuts], covered, style,
                         rules["effects"], _room(clips, rules))  # fmt: skip
    shots = _zoomed(shots, zooms)
    end = shots[-1].t_end if shots else 0.0
    m = plan.music
    music = MusicTrack(track=m.track, audio_in_sec=m.audio_in_sec, loop_sec=m.loop_sec,
                       fade_out_sec=float(r["music_fade_sec"])) if m else None  # fmt: skip
    cta = plan.blocks[-1].text
    words = [w for c in cuts for w in c.words]
    denoise, grade = _finishing(r)
    return Edl(
        style=style_name, width=int(size["width"]), height=int(size["height"]), fps=fps,
        video_kbps=int(size["video_kbps"]), shots=shots, content_end=end,
        end_card_sec=float(r["end_card_sec"]), duration=_frame(end + float(r["end_card_sec"]), fps),
        music=music, ambient_db=0.0, lufs=float(r["lufs"]), true_peak_db=float(r["true_peak_db"]),
        font=str(r["fonts"].get(style_name, r["fonts"]["default"])),
        captions=_cta(cuts, end, cta),
        mode="speech", overlays=[_broll_shot(b, clips, fps) for b in broll],
        duck=duck_intervals(cuts, float(se["duck_merge_gap_sec"])),
        duck_db=float(style["duck_db"]), duck_fade_sec=float(se["duck_fade_sec"]),
        karaoke=_karaoke_groups(words, se), denoise=denoise, grade=grade,
    )  # fmt: skip


def _cta(cuts: Sequence[SpeechCut], end: float, cta: str | None) -> list[Caption]:
    """The purpose's CTA line over the last phrase (the speaker says it, the screen shows it)."""
    if not cta or not cuts:
        return []
    return [Caption(start=cuts[-1].t_start, end=end, text=cta, kind="cta")]


def _karaoke_groups(words: Sequence[SpokenWord], se: Mapping[str, Any]) -> list[list[SpokenWord]]:
    return group_words(words, se["karaoke"], str(se["sentence_end"]))
