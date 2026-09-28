"""Edit decision list (phase 5a): one checked draft → everything ffmpeg needs, in frames.

Pure code. Cut points come from snap_to_beats (phase 4a) and are never moved here: a
dissolve borrows extra frames from the END of the outgoing shot, so the incoming shot still
starts on its beat (xfade overlaps the two, it does not shift the timeline).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from gani_dung_video.domain.drafts import SnappedDraft
from gani_dung_video.domain.footage import Clip
from gani_dung_video.domain.plan import Plan
from gani_dung_video.domain.preflight import Rules


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
    kind: str  # text | cta


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


def _frame(t: float, fps: int) -> float:
    return round(t * fps) / fps


def _dissolves(segs: Sequence[Any], style: Mapping[str, Any], fps: int) -> list[float]:
    """Seconds of cross-fade out of each shot: everywhere, only between blocks, or never."""
    n = len(segs)
    if style.get("transition") == "dissolve_all":
        d = int(style.get("dissolve_frames", 0)) / fps
        return [d if i < n - 1 else 0.0 for i in range(n)]
    between = int(style.get("dissolve_between_blocks", 0)) / fps
    return [between if i < n - 1 and segs[i].block != segs[i + 1].block else 0.0
            for i in range(n)]  # fmt: skip


def _shots(draft: SnappedDraft, style: Mapping[str, Any], clips: Mapping[str, Clip], fps: int
           ) -> list[Shot]:  # fmt: skip
    segs = draft.segments
    out: list[Shot] = []
    for seg, d in zip(segs, _dissolves(segs, style, fps), strict=True):
        source = _source(seg, clips)
        clip = clips[source]
        want = seg.src_out + d
        src_out = min(want, clip.duration_sec)
        out.append(Shot(source=source, src_in=_frame(seg.src_in, fps), src_out=_frame(src_out, fps),
                        t_start=_frame(seg.t_start, fps), t_end=_frame(seg.t_end, fps),
                        block=seg.block, hdr=clip.is_hdr, dissolve_out_sec=_frame(d, fps),
                        pad_sec=_frame(max(0.0, want - src_out), fps)))  # fmt: skip
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
    out: list[Caption] = []
    for seg in draft.segments:
        if not seg.text.strip():
            continue
        end = seg.t_end
        if per_block or seg.block == cta:
            end = max(s.t_end for s in draft.segments if s.block == seg.block)
        kind = "cta" if seg.block == cta else "text"
        out.append(Caption(start=_frame(seg.t_start, fps), end=_frame(end, fps),
                           text=seg.text.strip(), kind=kind))  # fmt: skip
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
    shots = _shots(draft, style, clips, fps)
    content_end = shots[-1].t_end if shots else 0.0
    end_card = float(r["end_card_sec"])
    m = plan.music
    music = MusicTrack(track=m.track, audio_in_sec=m.audio_in_sec, loop_sec=m.loop_sec,
                       fade_out_sec=float(r["music_fade_sec"])) if m else None  # fmt: skip
    amb = style.get("ambient_db")
    return Edl(
        style=style_name, width=int(size["width"]), height=int(size["height"]), fps=fps,
        video_kbps=int(size["video_kbps"]), shots=shots, content_end=content_end,
        end_card_sec=end_card, duration=_frame(content_end + end_card, fps), music=music,
        ambient_db=float(amb) if amb is not None else None, lufs=float(r["lufs"]),
        true_peak_db=float(r["true_peak_db"]),
        font=str(r["fonts"].get(style_name, r["fonts"]["default"])),
        captions=_captions(draft, plan, fps),
    )  # fmt: skip
