"""Integration tests (5c-1): transcripts + inventory + options → edit/phrases.json, and CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gani_dung_video.application.speech_service import build_phrases
from gani_dung_video.cli import main
from gani_dung_video.domain.speech import SpokenWord, Transcript
from gani_dung_video.infrastructure.config_files import ConfigError, load_glossary, load_rules
from gani_dung_video.infrastructure.footage_fs import EDIT_DIR, INVENTORY_FILE

RULES = load_rules()
GLOSSARY = load_glossary()


def clip_json(name: str, width: int = 576, height: int = 1024) -> dict[str, Any]:
    return {"file": name, "size_bytes": 1, "duration_sec": 60.0, "width": width,
            "height": height, "rotation": 0, "orientation": "doc", "fps": 30.0,
            "video_codec": "h264", "pix_fmt": "yuv420p", "color_transfer": None,
            "color_primaries": None, "is_hdr": False, "has_audio_stream": True,
            "audio_tracks": 1}  # fmt: skip


def words(spec: str, start: float = 0.0, step: float = 0.3, gaps: dict[int, float] | None = None
          ) -> list[SpokenWord]:  # fmt: skip
    out, t = [], start
    for i, w in enumerate(spec.split()):
        t += (gaps or {}).get(i, 0.0)
        out.append(SpokenWord(text=w, start=round(t, 3), end=round(t + step, 3), prob=0.9))
        t += step
    return out


def write_transcript(folder: Path, clip: str, text: str, no_speech: bool = False,
                     gaps: dict[int, float] | None = None) -> None:  # fmt: skip
    ws = words(text, gaps=gaps) if text else []
    t = Transcript(file=clip, model="test", cache_key="k", language="vi",
                   duration_sec=60.0, speech_sec=sum(w.end - w.start for w in ws),
                   word_count=len(ws), no_speech=no_speech, text=" ".join(w.text for w in ws),
                   words=ws)  # fmt: skip
    d = folder / EDIT_DIR / "transcripts"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{clip}.json").write_text(t.model_dump_json(indent=1), encoding="utf-8")


def write_inventory(folder: Path, clips: list[str]) -> None:
    d = folder / EDIT_DIR
    d.mkdir(parents=True, exist_ok=True)
    inv = {"folder": str(folder), "rules_version": str(RULES["rules_version"]),
          "clips": [clip_json(c) for c in clips], "duplicates": [], "skipped": [],
          "total_duration_sec": 60.0 * len(clips)}  # fmt: skip
    (d / INVENTORY_FILE).write_text(json.dumps(inv), encoding="utf-8")


def write_options(path: Path, **kw: Any) -> Path:
    base = {"phong_cach": "ban_hang_co_loi", "muc_dich": "ban_hang", "do_dai_giay": 30,
           "so_ban_nhap": 2, "nhac": "khong_nhac"}  # fmt: skip
    path.write_text(json.dumps({**base, **kw}), encoding="utf-8")
    return path


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    f = tmp_path / "xuong"
    write_inventory(f, ["a.mp4", "b.mp4"])
    write_transcript(f, "a.mp4", "Xin chào các bạn hôm nay nhé. Giò ngon nhất Sài Gòn nha. "
                                 "Giò lụa GANI thơm ngon lắm đó.")  # fmt: skip
    write_transcript(f, "b.mp4", "", no_speech=True)
    return f


def test_phrases_from_a_speech_clip_are_kept_and_banned_ones_reported(
    folder: Path, tmp_path: Path
) -> None:
    opt = write_options(tmp_path / "o.json")
    result, out = build_phrases(opt, folder, RULES, GLOSSARY)
    assert out == folder.resolve() / EDIT_DIR / "phrases.json"
    assert result.style == "ban_hang_co_loi" and result.rules_version == str(RULES["rules_version"])
    assert [p.clip for p in result.phrases] == ["a.mp4", "a.mp4"]
    assert len(result.excluded) == 1 and result.excluded[0].reason == "tu_cam"
    assert "ngon nhất" not in " ".join(p.text for p in result.phrases)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["phrases"]) == 2 and len(data["excluded"]) == 1


def test_phrase_ids_stay_unique_when_two_clips_share_a_stem(tmp_path: Path) -> None:
    f = tmp_path / "xuong"
    write_inventory(f, ["IMG_1.MOV", "IMG_1.mp4", "b.mp4"])
    for clip in ("IMG_1.MOV", "IMG_1.mp4", "b.mp4"):
        write_transcript(f, clip, "Xin chào các bạn hôm nay nhé.")
    result, _ = build_phrases(write_options(tmp_path / "o.json"), f, RULES, GLOSSARY)
    ids = [p.id for p in result.phrases]
    assert len(ids) == 3 and len(set(ids)) == 3
    assert "b_p0" in ids  # a clip whose stem is its own keeps the id it always had


def test_no_speech_clips_contribute_nothing(folder: Path, tmp_path: Path) -> None:
    opt = write_options(tmp_path / "o.json")
    result, _ = build_phrases(opt, folder, RULES, GLOSSARY)
    assert all(p.clip != "b.mp4" for p in result.phrases)


def test_non_speech_style_is_refused(folder: Path, tmp_path: Path) -> None:
    opt = write_options(tmp_path / "o.json", phong_cach="ke_chuyen_nghe",
                       muc_dich="gioi_thieu_xuong")  # fmt: skip
    with pytest.raises(ConfigError, match="ke_chuyen_nghe"):
        build_phrases(opt, folder, RULES, GLOSSARY)


def test_missing_transcripts_dir_is_refused(tmp_path: Path) -> None:
    f = tmp_path / "no_transcripts"
    write_inventory(f, ["a.mp4"])
    opt = write_options(tmp_path / "o.json")
    with pytest.raises(ConfigError, match="transcribe"):
        build_phrases(opt, f, RULES, GLOSSARY)


def test_missing_inventory_is_refused(tmp_path: Path) -> None:
    opt = write_options(tmp_path / "o.json")
    with pytest.raises(ConfigError, match="inventory"):
        build_phrases(opt, tmp_path, RULES, GLOSSARY)


def test_cli_phrases_prints_a_summary(folder: Path, tmp_path: Path,
                                      capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip
    opt = write_options(tmp_path / "o.json")
    assert main(["phrases", str(opt), str(folder)]) == 0
    out = capsys.readouterr().out
    assert "2 câu dùng được" in out and "1 câu bị loại" in out and "phrases.json" in out


def test_cli_phrases_wrong_style_exit_2(folder: Path, tmp_path: Path) -> None:
    opt = write_options(tmp_path / "o.json", phong_cach="review_mon_an", muc_dich="ban_hang")
    assert main(["phrases", str(opt), str(folder)]) == 2
