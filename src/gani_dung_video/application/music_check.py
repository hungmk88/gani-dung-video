"""Music folder check (ticket 14/10/2026): which tracks of a folder can build which videos.

For every audio file directly inside the folder: its BPM (declared in library.json, or measured
here), and, for every beat-driven style and every length, whether the plan would fit (ok), have
its tail cut within the gap limit (cắt) or be stopped at delivery (chặn). The verdict is the
planner's own (`bpm_measure.levels_at`), at the track's exact BPM, not a lookup.

READ ONLY: nothing is written. Not library.json, not the folder, not the music cache, not the
repo. A track that is not in library.json is measured on the spot (seconds each on the Dell).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from gani_dung_video.domain.bpm_measure import levels_at
from gani_dung_video.domain.plan import PlanError
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.infrastructure.music_analysis import (
    MusicError,
    read_library,
    track_duration,
    track_info,
)
from gani_dung_video.infrastructure.music_index import MAX_TRACKS, audio_files

DEFAULT_DURATIONS = (15, 30, 45, 60)
Verdict = Literal["ok", "cắt", "chặn", "lưới"]  # lưới: no clear beat, the virtual grid is used
Source = Literal["library", "measured", "unknown"]
_LEVEL: dict[str, Verdict] = {"OK": "ok", "WARNING": "cắt", "BLOCKING": "chặn"}


@dataclass(frozen=True)
class CheckedTrack:
    file: str
    bpm: float | None
    source: Source
    duration_sec: float | None
    note: str | None  # why there is no BPM / length


@dataclass(frozen=True)
class PairResult:
    style: str
    style_name: str
    duration: int
    verdicts: Mapping[str, Verdict]  # file name -> verdict


@dataclass(frozen=True)
class MusicCheck:
    folder: Path
    has_library: bool
    tracks: list[CheckedTrack]
    orphans: list[str]  # declared in library.json, no such file in the folder
    capped: bool  # the folder holds more than MAX_TRACKS audio files
    pairs: list[PairResult]


def _check_track(
    p: Path, library: Mapping[str, Any], cfg: Mapping[str, Any]
) -> CheckedTrack:  # fmt: skip
    timeout = float(cfg["ffmpeg_timeout_sec"])
    declared = library.get(p.name)
    try:
        if declared is not None:
            return CheckedTrack(p.name, float(declared.bpm), "library",
                                track_duration(p, timeout), None)  # fmt: skip
        info = track_info(p, {}, cfg)
        if info is not None:
            return CheckedTrack(p.name, info.bpm, "measured", info.duration_sec, None)
        return CheckedTrack(p.name, None, "unknown", track_duration(p, timeout),
                            "không có nhịp rõ: dựng bằng lưới ảo")  # fmt: skip
    except MusicError as e:
        bpm = float(declared.bpm) if declared is not None else None
        return CheckedTrack(p.name, bpm, "library" if declared is not None else "unknown", None,
                            str(e))  # fmt: skip


def check_music_folder(
    folder: Path, rules: Rules, *, durations: Sequence[int] = DEFAULT_DURATIONS,
    progress: Callable[[str], None] | None = None,
) -> MusicCheck:  # fmt: skip
    """Check every track of `folder` against every beat-driven style and length.

    Raises:
        MusicError: the folder is missing or library.json is unreadable.
    """
    if not folder.is_dir():
        raise MusicError(f"Thư mục nhạc không tồn tại: {folder}")
    cfg = rules["music_analysis"]
    extensions = tuple(str(e).lower() for e in cfg["extensions"])
    library = read_library(folder)
    files = audio_files(folder, extensions)
    tracks: list[CheckedTrack] = []
    for i, p in enumerate(files, start=1):
        if progress is not None:
            progress(f"[{i}/{len(files)}] {p.name}")
        tracks.append(_check_track(p, library, cfg))
    names = {t.file for t in tracks}
    pairs: list[PairResult] = []
    for style, conf in rules["styles"].items():
        if conf.get("driver") == "speech":
            continue
        for d in durations:
            try:
                verdicts = {
                    t.file: _LEVEL[levels_at(rules, style, d, [t.bpm])[0]]
                    if t.bpm is not None else "lưới"
                    for t in tracks
                }  # fmt: skip
            except PlanError:
                continue
            pairs.append(PairResult(style, str(conf["name"]), d, verdicts))
    return MusicCheck(
        folder=folder, has_library=(folder / "library.json").is_file(), tracks=tracks,
        orphans=sorted(n for n in library if n not in names),
        capped=len(files) >= MAX_TRACKS, pairs=pairs,
    )  # fmt: skip


# ─────────────── text ───────────────


def _mmss(sec: float | None) -> str:
    if sec is None:
        return "-"
    s = round(sec)
    return f"{s // 60}:{s % 60:02d}"


def _bpm(t: CheckedTrack) -> str:
    if t.bpm is None:
        return "-"
    return f"{t.bpm:g}" if t.source == "library" else f"{t.bpm:.1f}"


def _table(head: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(head)]  # fmt: skip

    def line(cells: Sequence[str]) -> str:
        return "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)).rstrip()

    return [line(head), *(line(r) for r in rows)]


def _usable(names: Sequence[str], limit: int = 3) -> str:
    if not names:
        return "-"
    shown = ", ".join(n.rsplit(".", 1)[0] for n in names[:limit])
    return shown + (f" +{len(names) - limit} bài" if len(names) > limit else "")


def format_report(chk: MusicCheck, detail: bool = False) -> str:
    """The report as plain text for the terminal."""
    out = [f"Thư mục: {chk.folder}",
           f"{len(chk.tracks)} bài âm thanh; library.json: "
           f"{'có' if chk.has_library else 'không có'}"]  # fmt: skip
    if chk.capped:
        out.append(f"CẢNH BÁO: chỉ kiểm {MAX_TRACKS} bài đầu tiên.")
    if not chk.tracks:
        out += ["", "Không có file âm thanh nào (.mp3 .m4a .wav .aac .flac .ogg) trực tiếp trong "
                    "thư mục này."]  # fmt: skip
        return "\n".join(out)
    out += ["", "BÀI NHẠC"]
    rows = [[str(i), t.file, _bpm(t), t.source, _mmss(t.duration_sec), t.note or ""]
            for i, t in enumerate(chk.tracks, start=1)]  # fmt: skip
    out += _table(["#", "File", "BPM", "Nguồn", "Dài", "Ghi chú"], rows)
    out += ["", "KHẢ NĂNG DÙNG THEO PHONG CÁCH × ĐỘ DÀI",
            "  ok = khít · cắt = cắt đuôi, trong ±10% · chặn = bị chặn khi giao · "
            "lưới = không đo được nhịp, dùng lưới ảo"]  # fmt: skip
    rows = []
    for p in chk.pairs:
        v = list(p.verdicts.values())
        good = [n for n, x in p.verdicts.items() if x in ("ok", "lưới")]
        rows.append([p.style_name, f"{p.duration}s", str(v.count("ok")), str(v.count("cắt")),
                     str(v.count("chặn")), str(v.count("lưới")), _usable(good)])  # fmt: skip
    out += _table(["Phong cách", "Dài", "ok", "cắt", "chặn", "lưới", "Bài dùng được (ok, lưới)"],
                  rows)  # fmt: skip
    if detail:
        out += ["", "CHI TIẾT TỪNG BÀI"]
        for t in chk.tracks:
            out.append(f"{t.file}  ({_bpm(t)} BPM, {t.source}, {_mmss(t.duration_sec)})")
            durations = sorted({p.duration for p in chk.pairs})
            grid = []
            for style in dict.fromkeys(p.style for p in chk.pairs):
                cells = {p.duration: p.verdicts[t.file] for p in chk.pairs if p.style == style}
                name = next(p.style_name for p in chk.pairs if p.style == style)
                grid.append([name, *(cells.get(d, "-") for d in durations)])
            out += ["  " + ln for ln in _table(["", *(f"{d}s" for d in durations)], grid)]
    warnings = []
    if chk.orphans:
        warnings.append("library.json có bài không còn file: " + ", ".join(chk.orphans))
    if any(t.source == "measured" for t in chk.tracks):
        warnings.append("BPM 'measured' do máy đo, có thể ra nửa hoặc gấp đôi: nghe lại rồi khai "
                        "báo vào library.json nếu khác.")  # fmt: skip
    if any(t.note and t.source != "unknown" for t in chk.tracks):
        warnings.append("Có bài không đọc được độ dài: xem cột Ghi chú.")
    if warnings:
        out += ["", "LƯU Ý", *(f"- {w}" for w in warnings)]
    return "\n".join(out)
