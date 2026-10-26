"""Technical checks on the rendered file (phase 5b, spec §7.4 "soát kỹ thuật"). Pure code.

T1 container (size, fps, codecs, length) · T2 frame count · T3 loudness/true peak ·
T4 black frames · T5 freezes · T6 captions on screen at once · T7 caption too long.
Each finding says what happens next: "error" stops delivery, "warn" is reported, "fix"
names an automatic fix ("loudness" = two-pass normalisation, "font" = smaller captions).
"""

from __future__ import annotations

import itertools
import math
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from gani_dung_video.domain.edl import Caption, Edl
from gani_dung_video.domain.preflight import Rules

PLAY_W = 1080  # ASS layout width (subtitles are laid out at the final size)
WidthFn = Callable[[str, str, int], float]  # (text, font family, px) → width in PLAY_W pixels

_BLACK = re.compile(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)")
_FREEZE_START = re.compile(r"freeze_start:\s*([\d.]+)")
_FREEZE_END = re.compile(r"freeze_end:\s*([\d.]+)")
_LUFS = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+|-inf)\s*LUFS")
_PEAK = re.compile(r"True peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS")


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str  # T1..T8 (file checks), C1 (claims), E1-* (delivery checks, domain/delivery_checks)
    level: str  # error | warn | fix
    msg: str  # Vietnamese, shown to the editor
    at_sec: float | None = None
    fix: str | None = None  # loudness | font


class MediaFacts(BaseModel):
    """What ffprobe says about the output file."""

    width: int
    height: int
    fps: float
    duration: float
    frames: int
    video_codec: str
    pix_fmt: str
    audio_codec: str | None
    sample_rate: int | None
    channels: int | None


class Measures(BaseModel):
    """What one decoding pass (ebur128 + blackdetect + freezedetect) found."""

    lufs: float
    true_peak_db: float
    blacks: list[tuple[float, float]]
    freezes: list[tuple[float, float]]


def _num(text: str) -> float:
    return -math.inf if text == "-inf" else float(text)


def parse_measures(log: str, duration: float) -> Measures:
    """Read the ffmpeg log of the measuring pass.

    Raises:
        ValueError: the ebur128 summary is missing (the pass did not finish).
    """
    lufs, peak = _LUFS.search(log), _PEAK.search(log)
    if lufs is None or peak is None:
        raise ValueError("Không đọc được bảng tổng kết ebur128 trong log ffmpeg.")
    blacks = [(float(a), float(b)) for a, b in _BLACK.findall(log)]
    starts = [float(x) for x in _FREEZE_START.findall(log)]
    ends = [float(x) for x in _FREEZE_END.findall(log)]
    freezes = [(s, ends[i] if i < len(ends) else duration) for i, s in enumerate(starts)]
    return Measures(lufs=_num(lufs.group(1)), true_peak_db=_num(peak.group(1)), blacks=blacks,
                    freezes=freezes)  # fmt: skip


# ─────────────── T1, T2 ───────────────


def check_media(f: MediaFacts, e: Edl, cfg: Mapping[str, Any]) -> list[Finding]:
    """T1 container matches the EDL; T2 no dropped or duplicated frames."""
    slack = int(cfg["frame_slack"])
    # H.264 B-frame reordering can drop 2–3 trailing frames when -shortest cuts mid-GOP, so T2
    # allows max(floor, fps × seconds) frames. Rounded half up: round() would give 4 at 30 fps.
    frame_slack = max(slack, math.floor(f.fps * float(cfg["frame_slack_sec"]) + 0.5))
    problems = []
    if (f.width, f.height) != (e.width, e.height):
        problems.append(f"khung {f.width}×{f.height}, cần {e.width}×{e.height}")
    if abs(f.fps - e.fps) > 1e-3:
        problems.append(f"{f.fps:g} fps, cần {e.fps}")
    if abs(f.duration - e.duration) > (slack + 0.5) / e.fps:
        problems.append(f"dài {f.duration:.3f}s, cần {e.duration:.3f}s")
    if f.video_codec != "h264" or f.pix_fmt != "yuv420p":
        problems.append(f"video {f.video_codec}/{f.pix_fmt}, cần h264/yuv420p")
    if f.audio_codec != "aac" or f.sample_rate != 48000 or f.channels != 2:
        problems.append(f"âm thanh {f.audio_codec}/{f.sample_rate}/{f.channels} kênh, cần "
                        "aac/48000/2 kênh")  # fmt: skip
    out = [Finding(code="T1", level="error", msg="File xuất sai chuẩn: " + "; ".join(problems))
           ] if problems else []  # fmt: skip
    want = round(e.duration * e.fps)
    if not problems and abs(f.frames - want) > frame_slack:
        out.append(Finding(code="T2", level="error", msg=f"{f.frames} khung hình, cần {want} "
                                                         "(mất hoặc lặp khung)."))  # fmt: skip
    return out


# ─────────────── T3, T4, T5 ───────────────


def _shot_at(e: Edl, t: float) -> int:
    return next((i + 1 for i, s in enumerate(e.shots) if s.t_start <= t < s.t_end), len(e.shots))


def _outside(span: tuple[float, float], holes: Sequence[tuple[float, float]]) -> float:
    """Seconds of span not covered by the holes (holes do not overlap each other)."""
    a, b = span
    covered = sum(max(0.0, min(b, y) - max(a, x)) for x, y in holes)
    return (b - a) - covered


def check_measures(m: Measures, e: Edl, cfg: Mapping[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    tol, peak_max = float(cfg["lufs_tolerance"]), float(cfg["max_true_peak_db"])
    planned_silent = e.music is None and e.ambient_db is None  # e.g. TVC with no music
    if not math.isfinite(m.lufs) or m.lufs <= float(cfg["silence_lufs"]):
        if not planned_silent:
            out.append(Finding(code="T3", level="error",
                               msg="Video không có tiếng dù có nhạc hoặc tiếng hiện trường "
                                   "(kiểm tra file nhạc và clip nguồn)."))  # fmt: skip
    elif abs(m.lufs - e.lufs) > tol:
        out.append(Finding(code="T3", level="fix", fix="loudness",
                           msg=f"Âm lượng {m.lufs:g} LUFS, đỉnh {m.true_peak_db:g} dBTP (cần "
                               f"{e.lufs:g} ±{tol:g} LU, đỉnh ≤ {peak_max:g})."))  # fmt: skip
    elif m.true_peak_db > peak_max:  # loudness right: the encoder made the peak, not the mix
        out.append(Finding(code="T3", level="fix", fix="peak",
                           msg=f"Âm lượng đạt ({m.lufs:g} LUFS) nhưng đỉnh thật {m.true_peak_db:g} "
                               f"dBTP > {peak_max:g} sau khi mã hóa — dựng lại với trần đỉnh thấp "
                               "hơn, hoặc tăng bitrate âm thanh trong rules.json."))  # fmt: skip
    # A planned fade-out to black (music mode cut at the chosen length) or a closing end wipe
    # (iris, fade to black) is not a black frame bug.
    end_card = [(e.content_end - max(e.trim_fade_sec, e.end_wipe_sec), e.duration)]
    for a, b in m.blacks:
        if _outside((a, b), end_card) >= float(cfg["black_min_sec"]):
            out.append(Finding(code="T4", level="error", at_sec=a,
                               msg=f"Khung đen {a:g}–{min(b, e.content_end):g}s ở cảnh "
                                   f"{_shot_at(e, a)}."))  # fmt: skip
    planned = [(s.t_end - s.pad_sec, s.t_end) for s in e.shots if s.pad_sec > 0] + end_card
    for a, b in m.freezes:
        if _outside((a, b), planned) >= float(cfg["freeze_min_sec"]):
            out.append(Finding(code="T5", level="warn", at_sec=a,
                               msg=f"Hình đứng yên {a:g}–{b:g}s ở cảnh {_shot_at(e, a)} "
                                   "(kiểm tra bằng mắt; cảnh quay tĩnh thì bỏ qua)."))  # fmt: skip
    return out


# ─────────────── T6, T7 ───────────────


def wrap_lines(text: str, max_width: float, width: Callable[[str], float]) -> list[str]:
    """Greedy word wrap, like libass smart wrapping; a single long word stays on its line."""
    lines: list[str] = []
    for word in text.split():
        if lines and width(f"{lines[-1]} {word}") <= max_width:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    return lines


def usable_width(rules: Rules) -> float:
    """Pixels a caption line may use: the frame minus the left and right layout margins."""
    safe = rules["render"]["safe"]
    return float(PLAY_W - int(safe["left"]) - int(safe["right"]))


_SIZE_KEY = {"cta": "cta", "title": "chapter_title"}  # every other kind is "caption"


def fit_keys(e: Edl) -> tuple[str, ...]:
    """The font sizes T7 may step down for this video: the chapter title only when it has one."""
    has_title = any(c.kind == "title" for c in e.captions)
    return ("caption", "cta", "chapter_title") if has_title else ("caption", "cta")


def size_key(kind: str) -> str:
    """Which font size of rules.render.font_px a caption of this kind uses."""
    return _SIZE_KEY.get(kind, "caption")


def _overflowing(
    e: Edl, rules: Rules, px: Mapping[str, int], width: WidthFn
) -> list[tuple[Caption, int, int]]:
    """(caption, lines it needs, px it was measured at) for every caption over the line limit."""
    max_lines, usable = int(rules["qc"]["max_lines"]), usable_width(rules)
    out = []
    for c in e.captions:
        size = int(px[size_key(c.kind)])

        def measure_line(t: str, s: int = size) -> float:
            return width(t, e.font, s)

        lines = wrap_lines(c.text, usable, measure_line)
        if len(lines) > max_lines:
            out.append((c, len(lines), size))
    return out


def _too_long(e: Edl, rules: Rules, px: Mapping[str, int], width: WidthFn) -> list[Finding]:
    max_lines = int(rules["qc"]["max_lines"])
    return [Finding(code="T7", level="fix", fix="font", at_sec=c.start,
                    msg=f"Chữ '{c.text}' cần {lines} dòng ở {size}px, tối đa {max_lines}.")
            for c, lines, size in _overflowing(e, rules, px, width)]  # fmt: skip


def check_captions(e: Edl, rules: Rules, px: Mapping[str, int], width: WidthFn) -> list[Finding]:
    """T6 two captions on screen at once; T7 a caption needs more than the allowed lines.

    A chapter title is drawn at the top of the frame, the other captions at the bottom: they
    are two lanes, and only captions in the same lane can overlap."""
    out = []
    for lane in (
        [c for c in e.captions if c.kind == "title"],
        [c for c in e.captions if c.kind != "title"],
    ):
        for a, b in itertools.pairwise(sorted(lane, key=lambda c: c.start)):
            if b.start < a.end - 1e-6:
                out.append(
                    Finding(
                        code="T6",
                        level="error",
                        at_sec=b.start,
                        msg=f"Chữ '{a.text}' và '{b.text}' hiện chồng lên nhau.",
                    )
                )
    return out + _too_long(e, rules, px, width)


def fit_font_px(
    e: Edl, rules: Rules, px: Mapping[str, int], width: WidthFn
) -> dict[str, int] | None:
    """Step down only the sizes whose captions overflow until every caption fits; None if
    even the floor (64 px) does not fit — then the text itself must be shortened."""
    step, floor = int(rules["qc"]["font_step_px"]), int(rules["qc"]["font_min_px"])
    sizes = dict(px)
    # The size to step down comes from the caption itself: two captions may start together (a
    # chapter title and the first caption of its chapter), so the start time is not a key.
    while found := _overflowing(e, rules, sizes, width):
        over = {size_key(c.kind) for c, _, _ in found}
        if all(sizes[k] <= floor for k in over):
            return None
        sizes = {k: max(floor, v - step) if k in over else v for k, v in sizes.items()}
    return sizes
