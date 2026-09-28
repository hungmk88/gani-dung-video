"""Unit tests: domain/footage.py — rules over ffprobe JSON, no video files needed."""

from __future__ import annotations

from typing import Any

import pytest

from gani_dung_video.domain.footage import (
    ProbeDataError,
    clip_from_probe,
    duplicate_candidates,
    orientation,
    parse_fps,
    rotation_of,
)
from gani_dung_video.infrastructure.config_files import load_rules

CFG = load_rules()["footage"]
HDR = CFG["hdr_transfers"]
PATTERNS = CFG["duplicate_patterns"]


def probe(
    video: dict[str, Any] | None = None, audio: int = 1, duration: str = "12.5"
) -> dict[str, Any]:
    v = {"codec_type": "video", "codec_name": "hevc", "width": 1920, "height": 1080,
         "avg_frame_rate": "30/1", "pix_fmt": "yuv420p"} | (video or {})  # fmt: skip
    streams = [v] + [{"codec_type": "audio"} for _ in range(audio)]
    return {"streams": streams, "format": {"duration": duration}}


def test_iphone_portrait_hlg_clip() -> None:
    # iPhone: stored 1920×1080 + display matrix -90°, HLG/BT.2020 when Video HDR is on.
    p = probe({"side_data_list": [{"rotation": -90}], "color_transfer": "arib-std-b67",
               "color_primaries": "bt2020", "pix_fmt": "yuv420p10le",
               "avg_frame_rate": "30000/1001"})  # fmt: skip
    c = clip_from_probe("IMG_8461.MOV", 123, p, HDR)
    assert (c.width, c.height, c.rotation, c.orientation) == (1080, 1920, 270, "doc")
    assert c.is_hdr and c.color_primaries == "bt2020" and c.fps == 29.97
    assert (c.has_audio_stream, c.audio_tracks, c.duration_sec) == (True, 1, 12.5)


def test_pq_is_hdr_and_bt709_is_not() -> None:
    assert clip_from_probe("a.mp4", 1, probe({"color_transfer": "smpte2084"}), HDR).is_hdr
    assert not clip_from_probe("b.mp4", 1, probe({"color_transfer": "bt709"}), HDR).is_hdr
    assert not clip_from_probe("c.mp4", 1, probe(), HDR).is_hdr  # no color metadata


def test_bt2020_primaries_alone_is_not_hdr() -> None:
    # Wide gamut without an HDR transfer curve is SDR; tone mapping would be wrong.
    assert not clip_from_probe("a.mp4", 1, probe({"color_primaries": "bt2020"}), HDR).is_hdr


def test_audio_track_count() -> None:
    assert clip_from_probe("a.mp4", 1, probe(audio=0), HDR).has_audio_stream is False
    c = clip_from_probe("a.mp4", 1, probe(audio=2), HDR)
    assert (c.has_audio_stream, c.audio_tracks) == (True, 2)


def test_duration_falls_back_to_stream_then_zero() -> None:
    p = probe({"duration": "3.25"}, duration="N/A")
    assert clip_from_probe("a.mp4", 1, p, HDR).duration_sec == 3.25
    p = {"streams": [probe()["streams"][0]], "format": {}}
    assert clip_from_probe("a.mp4", 1, p, HDR).duration_sec == 0.0


@pytest.mark.parametrize(
    "bad",
    [{"streams": [{"codec_type": "audio"}]}, {"streams": []}, {},
     {"streams": [{"codec_type": "video", "width": 0, "height": 0}]}],
)  # fmt: skip
def test_no_usable_video_stream(bad: dict[str, Any]) -> None:
    with pytest.raises(ProbeDataError):
        clip_from_probe("x.mp4", 1, bad, HDR)


@pytest.mark.parametrize(
    ("stream", "rot"),
    [({}, 0), ({"side_data_list": [{"rotation": -90}]}, 270),
     ({"side_data_list": [{"rotation": 90}]}, 90), ({"tags": {"rotate": "180"}}, 180),
     ({"side_data_list": [{"rotation": "bad"}]}, 0),
     ({"side_data_list": [{"displaymatrix": "x"}]}, 0)],
)  # fmt: skip
def test_rotation_of(stream: dict[str, Any], rot: int) -> None:
    assert rotation_of(stream) == rot


@pytest.mark.parametrize(
    ("rate", "fps"),
    [("30/1", 30.0), ("30000/1001", 29.97), ("0/0", 0.0), (None, 0.0), ("25", 0.0), ("a/b", 0.0)],
)
def test_parse_fps(rate: str | None, fps: float) -> None:
    assert parse_fps(rate) == fps


def test_fps_falls_back_to_r_frame_rate() -> None:
    p = probe({"avg_frame_rate": "0/0", "r_frame_rate": "60/1"})
    assert clip_from_probe("a.mp4", 1, p, HDR).fps == 60.0


def test_orientation() -> None:
    assert (orientation(1080, 1920), orientation(1920, 1080), orientation(1080, 1080)) == (
        "doc", "ngang", "vuong",
    )  # fmt: skip


def test_duplicate_candidates_follow_spec_patterns() -> None:
    names = ["IMG_8461.MOV", "IMG_8461 2.MOV", "clip.mp4", "clip (1).mp4", "Canh 2.mp4",
             "IMG_9000.mov", "IMG_9000 3.MOV", "X.mp4", "X 2.mov"]  # fmt: skip
    # "Canh 2.mp4" has no original; "X 2.mov" vs "X.mp4" differ in extension → not pairs.
    assert duplicate_candidates(names, PATTERNS) == [
        ("IMG_8461 2.MOV", "IMG_8461.MOV"),
        ("clip (1).mp4", "clip.mp4"),
        ("IMG_9000 3.MOV", "IMG_9000.mov"),  # extension compared case-insensitively
    ]


def test_no_candidates_without_original() -> None:
    assert duplicate_candidates(["Canh 2.mp4", "2.mp4", "Canh2.mp4"], PATTERNS) == []
