"""ffmpeg adapter (phase 5a): an EDL + ASS file → one MP4 (H.264 + AAC 48 kHz, -14 LUFS).

Each shot is read with input seeking (-ss/-t) and conformed to the output frame (cover +
crop, 30 fps, SDR). Cuts are joined with concat, dissolves with xfade whose offset is the
cut point, so every cut stays on its beat. Camera sound is placed on the timeline with
adelay (dissolves do not smear it), mixed with the music and loudness-normalised once.
The graph goes through a file (-/filter_complex) to stay clear of Windows command-line
limits and quoting; fonts are copied next to the .ass so libass finds them by a plain name.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from gani_dung_video.domain.edl import Edl, Shot
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.infrastructure.config_files import CONFIG_DIR
from gani_dung_video.infrastructure.ffprobe import FfprobeMissingError, ProbeError, probe

FONTS_DIR = CONFIG_DIR.parent / "assets" / "fonts"
SAMPLE_RATE = 48000
_HDR_TO_SDR = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
               "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv")  # fmt: skip


class RenderError(RuntimeError):
    """ffmpeg could not produce the video (message in Vietnamese, last ffmpeg line)."""


def _n(x: float) -> str:
    return f"{x:.6g}"


def _video_chain(i: int, s: Shot, e: Edl) -> str:
    parts = ["setpts=PTS-STARTPTS"]
    if s.hdr:
        parts.append(_HDR_TO_SDR)
    parts += [
        f"scale={e.width}:{e.height}:force_original_aspect_ratio=increase",
        f"crop={e.width}:{e.height}",
        "setsar=1",
        f"fps={e.fps}",
        "format=yuv420p",
    ]
    if s.pad_sec > 0:
        parts.append(f"tpad=stop_mode=clone:stop_duration={_n(s.pad_sec)}")
    return f"[{i}:v]{','.join(parts)}[v{i}]"


def _join_video(e: Edl) -> list[str]:
    n = len(e.shots)
    if all(s.dissolve_out_sec == 0 for s in e.shots):
        ins = "".join(f"[v{i}]" for i in range(n))
        return [f"{ins}concat=n={n}:v=1:a=0[vjoin]"]
    out, cur = [], "v0"
    for i in range(1, n):
        prev, nxt = e.shots[i - 1], f"j{i}" if i < n - 1 else "vjoin"
        if prev.dissolve_out_sec > 0:
            out.append(f"[{cur}][v{i}]xfade=transition=fade:duration={_n(prev.dissolve_out_sec)}"
                       f":offset={_n(prev.t_end)}[{nxt}]")  # fmt: skip
        else:
            out.append(f"[{cur}][v{i}]concat=n=2:v=1:a=0[{nxt}]")
        cur = nxt
    if n == 1:
        out.append("[v0]null[vjoin]")
    return out


def _audio(e: Edl, has_audio: Sequence[bool], music_idx: int | None) -> list[str]:
    fmt = f"aresample={SAMPLE_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"
    out = [f"anullsrc=r={SAMPLE_RATE}:cl=stereo,atrim=0:{_n(e.duration)}[abase]"]
    labels = ["[abase]"]
    if e.ambient_db is not None:
        for i, (s, has) in enumerate(zip(e.shots, has_audio, strict=True)):
            length, ms = s.t_end - s.t_start, round(s.t_start * 1000)
            src = (f"[{i}:a]{fmt},atrim=0:{_n(length)}" if has else
                   f"anullsrc=r={SAMPLE_RATE}:cl=stereo,atrim=0:{_n(length)}")  # fmt: skip
            out.append(f"{src},asetpts=PTS-STARTPTS,volume={_n(e.ambient_db)}dB,"
                       f"adelay={ms}|{ms}[a{i}]")  # fmt: skip
            labels.append(f"[a{i}]")
    if e.music is not None and music_idx is not None:
        m, chain = e.music, [f"[{music_idx}:a]{fmt}"]
        if m.loop_sec:
            chain += [f"atrim=0:{_n(m.loop_sec)}", "asetpts=PTS-STARTPTS",
                      f"aloop=loop=-1:size={round(m.loop_sec * SAMPLE_RATE)}"]  # fmt: skip
        chain += [f"atrim=0:{_n(e.duration)}", "asetpts=PTS-STARTPTS",
                  f"afade=t=out:st={_n(max(0.0, e.duration - m.fade_out_sec))}:"
                  f"d={_n(m.fade_out_sec)}"]  # fmt: skip
        out.append(",".join(chain) + "[amusic]")
        labels.append("[amusic]")
    out.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=first,"
               f"loudnorm=I={e.lufs}:TP={e.true_peak_db}:LRA=11,{fmt}[aout]")  # fmt: skip
    return out


def build_graph(
    e: Edl, has_audio: Sequence[bool], music_idx: int | None = None, ass_name: str | None = None
) -> str:
    """The -filter_complex text (pure: testable without ffmpeg)."""
    lines = [_video_chain(i, s, e) for i, s in enumerate(e.shots)]
    lines += _join_video(e)
    lines.append(f"color=c=black:s={e.width}x{e.height}:r={e.fps}:d={_n(e.end_card_sec)},"
                 "format=yuv420p,setsar=1[vend]")  # fmt: skip
    subs = f",subtitles=filename={ass_name}:fontsdir=fonts" if ass_name else ""
    lines.append(f"[vjoin][vend]concat=n=2:v=1:a=0{subs}[vout]")
    lines += _audio(e, has_audio, music_idx)
    return ";\n".join(lines)


def _has_audio(path: Path, timeout: float) -> bool:
    try:
        return any(s.get("codec_type") == "audio" for s in probe(path, timeout)["streams"])
    except (ProbeError, FfprobeMissingError, KeyError) as e:
        raise RenderError(f"ffmpeg không đọc được {path.name}: {e}") from e


def _copy_fonts(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for f in FONTS_DIR.glob("*.ttf"):
        target = dest / f.name
        if not target.is_file() or target.stat().st_size != f.stat().st_size:
            shutil.copyfile(f, target)


def encode_args(e: Edl, rules: Rules) -> list[str]:
    """Output codec settings (spec §7.5: H.264 30 fps yuv420p, AAC 48 kHz).

    -aac_pns 0: the native AAC encoder's perceptual noise substitution pushed a -2.6 dB mix
    to +6.8 dB peaks on real GANI footage (29/09/2026); without it peaks stay below 0 dB.
    """
    r, kbps = rules["render"], e.video_kbps
    return ["-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k",
            "-maxrate", f"{round(kbps * 1.2)}k", "-bufsize", f"{kbps * 2}k",
            "-pix_fmt", "yuv420p", "-r", str(e.fps),
            "-c:a", "aac", "-aac_pns", "0", "-b:a", f"{r['audio_kbps']}k",
            "-ar", str(r["audio_rate"]), "-movflags", "+faststart"]  # fmt: skip


def render(
    e: Edl, footage_dir: Path, music: Path | None, ass: Path, out: Path, rules: Rules
) -> None:
    """Run ffmpeg for one EDL. Work files (graph, fonts) live next to the .ass file.

    Raises:
        RenderError: a source is missing/unreadable, ffmpeg is missing, fails or times out.
    """
    r = rules["render"]
    work = ass.parent
    sources = [footage_dir / s.source for s in e.shots]
    for p in sources + ([music] if music else []):
        if not p.is_file():
            raise RenderError(f"Không tìm thấy {p.name} — ffmpeg không thể dựng.")
    timeout = float(r["ffmpeg_timeout_sec"])
    has_audio = [_has_audio(p, 60.0) for p in sources]
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for s, p in zip(e.shots, sources, strict=True):
        args += ["-ss", _n(s.src_in), "-t", _n(s.src_out - s.src_in), "-i", str(p)]
    music_idx = None
    if music is not None and e.music is not None:
        music_idx = len(sources)
        args += ["-ss", _n(e.music.audio_in_sec), "-i", str(music)]
    _copy_fonts(work / "fonts")
    graph = work / "graph.txt"
    graph.write_text(build_graph(e, has_audio, music_idx, ass.name), encoding="utf-8")
    args += ["-/filter_complex", graph.name, "-map", "[vout]", "-map", "[aout]",
             *encode_args(e, rules), "-t", _n(e.duration), str(out)]  # fmt: skip
    _run(args, work, timeout)


def extract_cover(video: Path, at_sec: float, out: Path, timeout: float = 60.0) -> None:
    """cover.jpg: one frame of the finished video."""
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", _n(at_sec), "-i",
          str(video), "-frames:v", "1", "-q:v", "2", str(out)], video.parent, timeout)  # fmt: skip


def _run(args: list[str], cwd: Path, timeout: float) -> None:
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as e:
        raise RenderError("Không tìm thấy ffmpeg. Cài ffmpeg rồi mở lại cửa sổ lệnh.") from e
    except subprocess.TimeoutExpired as e:
        raise RenderError(f"ffmpeg chạy quá {timeout:g}s, đã dừng.") from e
    if r.returncode != 0:
        lines = r.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RenderError(f"ffmpeg lỗi: {lines[-1] if lines else r.returncode}")
