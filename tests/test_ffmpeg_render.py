"""Tests (5a): infrastructure/ffmpeg_render.py — filter graph + a real short render."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gani_dung_video.domain.edl import Caption, Edl, MusicTrack, Shot
from gani_dung_video.domain.subtitles import ass_document
from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.ffmpeg_render import (
    RenderError,
    audio_graph,
    build_graph,
    encode_args,
    parse_loudnorm,
    render,
)

RULES = load_rules()
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="cần ffmpeg")


def shot(src: str, t0: float, t1: float, d: float = 0.0, hdr: bool = False, pad: float = 0.0,
         src_in: float = 0.0) -> Shot:  # fmt: skip
    return Shot(source=src, src_in=src_in, src_out=src_in + (t1 - t0) + d - pad, t_start=t0,
                t_end=t1, block="b", hdr=hdr, dissolve_out_sec=d, pad_sec=pad)  # fmt: skip


def edl(shots: list[Shot], music: MusicTrack | None = None, ambient: float | None = -20,
        preview: bool = True) -> Edl:  # fmt: skip
    end = shots[-1].t_end
    w, h = (540, 960) if preview else (1080, 1920)
    return Edl(
        style="ke_chuyen_nghe",
        width=w,
        height=h,
        fps=30,
        video_kbps=2500,
        shots=shots,
        content_end=end,
        end_card_sec=1.0,
        duration=end + 1.0,
        music=music,
        ambient_db=ambient,
        lufs=-14.0,
        true_peak_db=-1.5,
        font="Be Vietnam Pro ExtraBold",
        captions=[Caption(start=0, end=1, text="Một ngày ở xưởng", kind="text")],
    )


# ─────────────── graph (no ffmpeg needed) ───────────────


def test_cuts_are_concatenated_and_end_card_appended() -> None:
    g = build_graph(edl([shot("a.mp4", 0, 1), shot("b.mp4", 1, 2)]), has_audio=[True, True])
    assert "concat=n=2:v=1:a=0" in g and "xfade" not in g
    assert "color=c=black:s=540x960:r=30:d=1" in g
    assert "fps=30" in g and "scale=540:960:force_original_aspect_ratio=increase" in g


def test_dissolve_overlaps_at_the_cut_point() -> None:
    g = build_graph(edl([shot("a.mp4", 0, 1, d=0.4), shot("b.mp4", 1, 2)]), has_audio=[True, True])
    assert "xfade=transition=fade:duration=0.4:offset=1" in g


def test_hdr_shots_are_tone_mapped_and_short_sources_padded() -> None:
    g = build_graph(edl([shot("a.mp4", 0, 1, hdr=True, pad=0.2)]), has_audio=[True])
    assert "tonemap=" in g and "zscale" in g
    assert "tpad=stop_mode=clone:stop_duration=0.2" in g


def test_audio_is_placed_on_the_timeline_music_looped_and_loudness_normalised() -> None:
    m = MusicTrack(track="t.m4a", audio_in_sec=0.5, loop_sec=2.0, fade_out_sec=0.5)
    g = build_graph(edl([shot("a.mp4", 0, 1), shot("b.mp4", 1, 2)], music=m),
                    has_audio=[True, False], music_idx=2)  # fmt: skip
    assert "[2:a]" in g  # music is the input after the two shots
    assert "adelay=1000|1000" in g  # second shot's sound starts at 1.0 s
    assert "anullsrc" in g  # b.mp4 has no sound: silence of its length
    assert "volume=-20dB" in g
    assert "aloop=loop=-1" in g and "afade=t=out" in g
    assert "loudnorm=I=-14.0:TP=-1.5" in g


MEASURED = {
    "input_i": -20.3,
    "input_tp": -4.1,
    "input_lra": 3.2,
    "input_thresh": -30.5,
    "target_offset": 0.4,
}


def test_second_pass_applies_the_measured_values_linearly() -> None:
    g = build_graph(edl([shot("a.mp4", 0, 1)]), has_audio=[True], loudnorm=MEASURED)
    assert ("loudnorm=I=-14.0:TP=-1.5:LRA=11:measured_I=-20.3:measured_TP=-4.1:"
            "measured_LRA=3.2:measured_thresh=-30.5:offset=0.4:linear=true") in g  # fmt: skip


def test_first_pass_graph_is_audio_only_and_prints_json() -> None:
    g = audio_graph(edl([shot("a.mp4", 0, 1)]), [True], None, measure_only=True)
    assert "print_format=json" in g and "[v" not in g and "subtitles" not in g


LOUDNORM_LOG = """noise
[Parsed_loudnorm_5 @ 0]
{
	"input_i" : "-20.30",
	"input_tp" : "-4.10",
	"input_lra" : "3.20",
	"input_thresh" : "-30.50",
	"output_i" : "-14.0",
	"target_offset" : "0.40"
}
"""


def test_parse_loudnorm_json_from_the_ffmpeg_log() -> None:
    assert parse_loudnorm(LOUDNORM_LOG) == MEASURED
    with pytest.raises(RenderError, match="loudnorm"):
        parse_loudnorm("no json here")
    silent = LOUDNORM_LOG.replace('"-20.30"', '"-inf"')
    with pytest.raises(RenderError, match="im lặng"):
        parse_loudnorm(silent)  # never hand -inf to loudnorm's linear pass


def test_muted_style_without_music_still_has_a_silent_track() -> None:
    g = build_graph(edl([shot("a.mp4", 0, 1)], ambient=None), has_audio=[True])
    assert "volume=" not in g and "anullsrc" in g


def test_encoder_settings_meet_the_render_standard() -> None:
    args = encode_args(edl([shot("a.mp4", 0, 1)], preview=False), RULES)
    joined = " ".join(args)
    assert "-c:v libx264" in joined and "-pix_fmt yuv420p" in joined and "-r 30" in joined
    assert "-c:a aac" in joined and "-ar 48000" in joined and "-movflags +faststart" in joined
    # Found on real footage 29/09/2026: the native AAC encoder's noise substitution (PNS) turned
    # a -2.6 dB mix into +6.8 dB peaks (audible clipping). PNS off keeps it at -1.2 dB.
    assert "-aac_pns 0" in joined
    # 192 kbps AAC added up to 1.2 dB of true peak on real footage; 320 kbps added 0.1 dB.
    assert "-b:a 320k" in joined


# ─────────────── real render ───────────────


def ff(out: Path, graph: str, dur: float, audio: bool = True) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", graph]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}"]
    cmd += ["-t", str(dur), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    cmd += (["-c:a", "aac", "-shortest"] if audio else []) + [str(out)]
    subprocess.run(cmd, check=True, timeout=120)


def probe(path: Path) -> dict[str, object]:
    r = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    data: dict[str, object] = json.loads(r.stdout)
    return data


@needs_ffmpeg
def test_real_render_has_the_planned_size_length_fps_and_audio(tmp_path: Path) -> None:
    ff(tmp_path / "a.mp4", "testsrc2=s=720x1280:r=30:d=3", 3.0)
    ff(tmp_path / "b.mp4", "smptebars=s=1280x720:r=25:d=3", 3.0, audio=False)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=220:duration=4", "-c:a", "aac", str(tmp_path / "t.m4a")],
                   check=True, timeout=60)  # fmt: skip
    e = edl(
        [shot("a.mp4", 0, 1.0, d=0.4, src_in=0.5), shot("b.mp4", 1.0, 2.0)],
        music=MusicTrack(track="t.m4a", audio_in_sec=0.0, loop_sec=None, fade_out_sec=0.5),
    )
    ass = tmp_path / "sub.ass"
    ass.write_text(ass_document(e, RULES), encoding="utf-8")
    out = tmp_path / "out.mp4"
    render(e, tmp_path, tmp_path / "t.m4a", ass, out, RULES)
    info = probe(out)
    streams = info["streams"]
    assert isinstance(streams, list)
    v = next(s for s in streams if s["codec_type"] == "video")
    a = next(s for s in streams if s["codec_type"] == "audio")
    assert (v["width"], v["height"], v["r_frame_rate"]) == (540, 960, "30/1")
    assert a["sample_rate"] == "48000"
    fmt = info["format"]
    assert isinstance(fmt, dict) and float(fmt["duration"]) == pytest.approx(3.0, abs=0.1)


@needs_ffmpeg
def test_ffmpeg_failure_is_reported(tmp_path: Path) -> None:
    e = edl([shot("missing.mp4", 0, 1)])
    ass = tmp_path / "sub.ass"
    ass.write_text(ass_document(e, RULES), encoding="utf-8")
    with pytest.raises(RenderError, match="ffmpeg"):
        render(e, tmp_path, None, ass, tmp_path / "out.mp4", RULES)
