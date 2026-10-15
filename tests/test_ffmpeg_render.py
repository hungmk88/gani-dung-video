"""Tests (5a): infrastructure/ffmpeg_render.py — filter graph + a real short render."""

from __future__ import annotations

import json
import math
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from ffmpeg_marks import needs_render_ffmpeg

from gani_dung_video.domain.edl import Caption, Edl, MusicTrack, Shot
from gani_dung_video.domain.subtitles import ass_document
from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.ffmpeg_render import (
    APP_DIR,
    RenderError,
    audio_graph,
    brand_logo,
    build_graph,
    encode_args,
    logo_files,
    parse_loudnorm,
    render,
)
from gani_dung_video.infrastructure.ffmpeg_render import _run as render_run

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


def test_a_cut_to_length_fades_picture_and_sound_over_the_last_seconds() -> None:
    cut = edl([shot("a.mp4", 0, 1), shot("b.mp4", 1, 4)]).model_copy(update={"trim_fade_sec": 0.5})
    g = build_graph(cut, has_audio=[True, True])
    assert "fade=t=out:st=3.5:d=0.5[vfade]" in g and "[vfade][vend]concat" in g
    mix = g.split("amix=")[1].split("[aout]")[0]
    assert "afade=t=out:st=3.5:d=0.5," in mix and mix.index("afade") < mix.index("loudnorm")


def test_without_a_cut_there_is_no_fade() -> None:
    g = build_graph(edl([shot("a.mp4", 0, 1), shot("b.mp4", 1, 4)]), has_audio=[True, True])
    assert "fade=t=out" not in g.replace("afade=t=out", "")
    assert "[vfade]" not in g and "afade=t=out:st=3.5" not in g


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


def test_denoise_cleans_only_the_camera_sound_before_anything_is_mixed() -> None:
    m = MusicTrack(track="t.m4a", audio_in_sec=0.0, loop_sec=None, fade_out_sec=0.5)
    base = edl([shot("a.mp4", 0, 1), shot("b.mp4", 1, 2)], music=m)
    off = build_graph(base, has_audio=[True, False], music_idx=2)
    assert "afftdn" not in off
    on = build_graph(base.model_copy(update={"denoise": "afftdn=nr=12:nf=-40"}),
                     has_audio=[True, False], music_idx=2)  # fmt: skip
    assert on.count("afftdn=nr=12:nf=-40") == 1  # one shot has sound; the silent one has none
    line = next(ln for ln in on.splitlines() if ln.startswith("[0:a]"))
    assert line.index("afftdn") < line.index("volume=") < line.index("adelay=")  # voice layer only
    assert "afftdn" not in next(ln for ln in on.splitlines() if ln.startswith("[2:a]"))  # music
    assert on.index("amix=") > on.index("afftdn") and "afftdn" not in on.split("amix=")[1]


def test_grade_goes_on_the_picture_only_not_on_the_captions_or_the_end_card() -> None:
    base = edl([shot("a.mp4", 0, 1), shot("b.mp4", 1, 2)])
    assert "[vgrade]" not in build_graph(base, has_audio=[True, True], ass_name="s.ass")
    tone = "eq=saturation=1.06:contrast=1.016,lutyuv=u='val-1.6':v='val+1.6'"
    g = build_graph(base.model_copy(update={"grade": tone}), has_audio=[True, True],
                    ass_name="s.ass")  # fmt: skip
    assert f"[vjoin]{tone}[vgrade]" in g
    assert "[vgrade][vend]concat=n=2:v=1:a=0,subtitles=" in g  # captions drawn after the grade


# ─────────────── picture logo (14/10/2026) ───────────────

BRAND = RULES["render"]["brand"]


def at(expr: str, t: float) -> float:
    """Evaluate the overlay x/y expression the way ffmpeg does at time t."""
    py = expr.replace("if(", "IF(").replace("eq(", "EQ(").replace("mod(", "MOD(")
    env = {"IF": lambda c, a, b: a if c else b, "EQ": lambda a, b: a == b,
           "MOD": lambda a, b: a % b, "floor": math.floor, "t": t}  # fmt: skip
    return float(eval(py, env))  # test-only: the expression is built by our own code


def logo_overlay(g: str) -> tuple[str, str, str]:
    m = re.search(r"\[logo\]overlay=x='([^']*)':y='([^']*)':enable='([^']*)'", g)
    assert m, g
    return m.group(1), m.group(2), m.group(3)


def png_size(path: Path) -> tuple[int, int]:
    w, h = struct.unpack(">II", path.read_bytes()[16:24])
    return int(w), int(h)


def test_logo_appears_at_5_s_and_hops_to_the_next_place_every_4_s() -> None:
    e = edl([shot("a.mp4", 0, 10), shot("b.mp4", 10, 20)], preview=False)
    g = build_graph(e, has_audio=[True, True], ass_name="s.ass", logo=brand_logo(RULES, 2))
    x, y, enable = logo_overlay(g)
    assert enable == "gte(t,5)"
    places = [tuple(p) for p in BRAND["logo_positions"]]
    assert len(set(places)) >= 2  # it never sits still
    seen = [(at(x, t), at(y, t)) for t in (5.0, 8.9, 9.0, 13.0, 17.0, 21.0)]
    assert seen == [places[0], places[0], places[1], places[2 % len(places)],
                    places[3 % len(places)], places[4 % len(places)]]  # fmt: skip
    assert "[2:v]format=rgba,scale=170:-1,colorchannelmixer=aa=0.8[logo]" in g
    assert "[3:v]format=rgba,scale=520:-1[endlogo]" in g
    assert "[vend0][endlogo]overlay=x=(W-w)/2:y=420" in g
    assert "[vlogo][vend]concat=n=2:v=1:a=0,subtitles=" in g  # captions still on top


def test_draft_logo_is_laid_out_at_half_size() -> None:
    g = build_graph(edl([shot("a.mp4", 0, 10)]), has_audio=[True], logo=brand_logo(RULES, 1))
    x, y, _ = logo_overlay(g)
    x0, y0 = BRAND["logo_positions"][0]
    assert (at(x, 5.0), at(y, 5.0)) == (round(x0 / 2), round(y0 / 2))
    assert "scale=84:-1" in g or "scale=86:-1" in g  # 170 px at 1080 wide → about 85 at 540


def test_without_a_picture_logo_the_graph_is_unchanged() -> None:
    g = build_graph(edl([shot("a.mp4", 0, 1)]), has_audio=[True])
    assert "[logo]" not in g and "[vend0]" not in g and "[vfade][vend]" not in g


def test_logo_places_stay_clear_of_titles_captions_and_the_side_buttons() -> None:
    r = RULES["render"]
    safe = r["safe"]
    wm, card = logo_files(RULES) or pytest.fail("rules.json names no picture logo")
    w0, h0 = png_size(wm)
    w, h = BRAND["logo_width"], BRAND["logo_width"] * h0 / w0
    title_top = safe["top"] + r["chapter_title_offset"]
    captions_top = 1920 - safe["bottom"] - 2 * r["font_px"]["caption"]  # two caption lines
    for x, y in BRAND["logo_positions"]:
        assert safe["left"] <= x and x + w <= 1080 - safe["right"]
        assert safe["top"] <= y and y + h <= captions_top
        if y < title_top:  # top row: ends above the chapter title line
            assert y + h <= title_top
    cw0, ch0 = png_size(card)
    bottom = BRAND["end_card_logo_top"] + BRAND["end_card_logo_width"] * ch0 / cw0
    zalo_top = 1020 - r["font_px"]["end_card_text"] / 2  # EndCardText is centred at y = 1020
    assert BRAND["end_card_logo_top"] >= safe["top"] and bottom < zalo_top


def test_a_missing_logo_picture_stops_the_render_with_its_name() -> None:
    brand = {**BRAND, "logo_image": "assets/brand/khong-co.png"}
    rules = {**RULES, "render": {**RULES["render"], "brand": brand}}
    with pytest.raises(RenderError, match=r"khong-co\.png"):
        logo_files(rules)
    assert APP_DIR.joinpath(BRAND["logo_image"]).is_file()


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


def test_speech_mode_overlays_broll_and_ducks_the_music() -> None:
    m = MusicTrack(track="t.m4a", audio_in_sec=0.0, loop_sec=None, fade_out_sec=0.5)
    e = edl([shot("a.mp4", 0, 2), shot("a.mp4", 2, 4)], music=m, ambient=0.0)
    e = e.model_copy(update={"mode": "speech", "duck": [(0.1, 1.8)], "duck_db": -18.0,
                             "overlays": [shot("b.mp4", 2.4, 3.6)]})  # fmt: skip
    g = build_graph(e, has_audio=[True, True], music_idx=3)
    assert "[2:v]" in g  # the overlay is the input after the two A-roll shots
    assert "setpts=PTS-STARTPTS+2.4/TB" in g
    assert "overlay=eof_action=pass:enable='between(t,2.4,3.6)'" in g
    assert "volume='1-0.87" in g and ":eval=frame" in g  # -18 dB duck curve on the music
    assert "[2:a]" not in g  # B-roll sound is never mixed under the voice


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


@needs_render_ffmpeg
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


def grey_at(video: Path, t: float, box: tuple[int, int, int, int]) -> float:
    """Share of pixels inside box (x, y, w, h) still the plain grey of the test source."""
    x, y, w, h = box
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(t), "-i",
                        str(video), "-frames:v", "1", "-vf", f"crop={w}:{h}:{x}:{y}", "-f",
                        "rawvideo", "-pix_fmt", "gray", "-"],
                       capture_output=True, check=True, timeout=60)  # fmt: skip
    return sum(1 for v in r.stdout if abs(v - 128) <= 6) / len(r.stdout)


@needs_render_ffmpeg
def test_real_render_moves_the_picture_logo_and_puts_it_on_the_end_card(tmp_path: Path) -> None:
    ff(tmp_path / "a.mp4", "color=c=0x808080:s=360x640:r=30:d=11", 11.0)
    e = edl([shot("a.mp4", 0, 10.0)]).model_copy(update={"captions": []})
    ass = tmp_path / "sub.ass"
    ass.write_text(ass_document(e, RULES), encoding="utf-8")
    out = tmp_path / "out.mp4"
    render(e, tmp_path, None, ass, out, RULES)
    (x0, y0), (x1, y1) = BRAND["logo_positions"][:2]
    first = (round(x0 / 2) + 10, round(y0 / 2) + 10, 60, 30)  # inside the 85 px draft logo
    second = (round(x1 / 2) + 10, round(y1 / 2) + 10, 60, 30)
    assert grey_at(out, 2.0, first) > 0.99  # nothing before 5 s
    assert grey_at(out, 6.0, first) < 0.7 and grey_at(out, 6.0, second) > 0.99
    assert grey_at(out, 9.5, second) < 0.7 and grey_at(out, 9.5, first) > 0.99  # hopped at 9 s
    card = (240, 230, 60, 60)  # centre of the large logo on the black end card
    probe_card = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", "10.5",
                                 "-i", str(out), "-frames:v", "1", "-vf",
                                 "crop={2}:{3}:{0}:{1}".format(*card), "-f", "rawvideo",
                                 "-pix_fmt", "gray", "-"],
                                capture_output=True, check=True, timeout=60)  # fmt: skip
    assert max(probe_card.stdout) > 60  # not black: the logo is there


@needs_render_ffmpeg
def test_real_speech_render_with_broll_overlay_and_ducked_music(tmp_path: Path) -> None:
    ff(tmp_path / "a.mp4", "testsrc2=s=360x640:r=30:d=5", 5.0)  # the "speaker"
    ff(tmp_path / "b.mp4", "smptebars=s=640x360:r=30:d=5", 5.0, audio=False)  # B-roll
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "sine=frequency=220:duration=8", "-c:a", "aac", str(tmp_path / "t.m4a")],
                   check=True, timeout=60)  # fmt: skip
    m = MusicTrack(track="t.m4a", audio_in_sec=0.0, loop_sec=None, fade_out_sec=0.5)
    e = edl([shot("a.mp4", 0, 2.0), shot("a.mp4", 2.0, 4.0, src_in=2.5)], music=m, ambient=0.0)
    e = e.model_copy(update={"mode": "speech", "duck": [(0.2, 1.8), (2.2, 3.8)],
                             "duck_db": -18.0, "overlays": [shot("b.mp4", 2.4, 3.4)]})  # fmt: skip
    ass = tmp_path / "sub.ass"
    ass.write_text(ass_document(e, RULES), encoding="utf-8")
    out = tmp_path / "speech.mp4"
    render(e, tmp_path, tmp_path / "t.m4a", ass, out, RULES)
    info = probe(out)
    fmt = info["format"]
    assert isinstance(fmt, dict) and float(fmt["duration"]) == pytest.approx(5.0, abs=0.1)


@needs_ffmpeg
def test_ffmpeg_failure_is_reported(tmp_path: Path) -> None:
    e = edl([shot("missing.mp4", 0, 1)])
    ass = tmp_path / "sub.ass"
    ass.write_text(ass_document(e, RULES), encoding="utf-8")
    with pytest.raises(RenderError, match="ffmpeg"):
        render(e, tmp_path, None, ass, tmp_path / "out.mp4", RULES)


# ─────────────── a crossfade after a hard cut (found by the real API run, 30/09/2026) ───────


MIXED = [
    shot("a.mp4", 0, 1.0, d=0.3, src_in=0.0),  # dissolve out …
    shot("a.mp4", 1.0, 2.0, src_in=1.5),  # … then two hard cuts …
    shot("a.mp4", 2.0, 3.0, src_in=3.0),
    shot("a.mp4", 3.0, 4.0, d=0.3, src_in=4.5),  # … then a dissolve again
    shot("a.mp4", 4.0, 5.0, src_in=6.0),
]


def test_a_cut_gets_its_timebase_reset_so_a_later_crossfade_can_follow() -> None:
    g = build_graph(edl(list(MIXED)), has_audio=[True] * 5)
    joins = [ln for ln in g.splitlines() if "concat=n=2:v=1:a=0" in ln and "[vend]" not in ln]
    assert len(joins) == 2  # the two hard cuts between the crossfades
    assert all("concat=n=2:v=1:a=0,settb=1/30[" in ln for ln in joins)


def test_pure_cuts_and_pure_crossfades_keep_their_graph() -> None:
    cuts = build_graph(edl([shot("a.mp4", 0, 1), shot("b.mp4", 1, 2)]), has_audio=[True, True])
    assert "settb" not in cuts  # one concat over every shot: nothing to reset


@needs_render_ffmpeg
def test_real_render_survives_crossfade_cut_cut_crossfade(tmp_path: Path) -> None:
    ff(tmp_path / "a.mp4", "testsrc2=s=720x1280:r=30:d=8", 8.0)
    e = edl(list(MIXED))
    ass = tmp_path / "sub.ass"
    ass.write_text(ass_document(e, RULES), encoding="utf-8")
    out = tmp_path / "mixed.mp4"
    render(e, tmp_path, None, ass, out, RULES)
    fmt = probe(out)["format"]
    assert isinstance(fmt, dict) and float(fmt["duration"]) == pytest.approx(6.0, abs=0.2)


def test_an_ffmpeg_failure_keeps_the_last_lines_so_the_cause_is_visible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stderr = "\n".join(f"line{i}" for i in range(1, 13)).encode()
    done = subprocess.CompletedProcess(["ffmpeg"], 4294967274, b"", stderr)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: done)
    with pytest.raises(RenderError) as caught:
        render_run(["ffmpeg"], tmp_path, 5.0)
    msg = str(caught.value)
    assert "line12" in msg and "line5" in msg and "line4" not in msg
