"""Integration tests (4b): plan.json + scenes.json + cached vectors + note → candidates.json.

No torch: frame vectors are written as the 3c cache would, dish prompts use a fake encoder.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from gani_dung_video.application.pick_service import build_pick
from gani_dung_video.cli import main
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.plan import compile_plan
from gani_dung_video.domain.scenes import Scene, ScenesFile, Segment, SimilarPair
from gani_dung_video.domain.semantics import Vectors
from gani_dung_video.infrastructure.config_files import ConfigError, load_glossary, load_rules

RULES = load_rules()
GLOSSARY = load_glossary()
STYLE = "ke_chuyen_nghe"
MEATBALL = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
MACHINE = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
BO_VIEN = set(GLOSSARY.visual["bò viên"])


def fake_texts(texts: Sequence[str], calls: list[int] | None = None) -> Vectors:
    if calls is not None:
        calls.append(len(texts))
    rows = [MEATBALL if t in BO_VIEN else np.array([0.0, 0.0, 1.0, 0.0]) for t in texts]
    return np.stack(rows).astype(np.float32)


def seg(start: float, labels: list[str], aes: float) -> Segment:
    return Segment(start=start, end=start + 5.0, peak=start + 2.0, aesthetic=aes, labels=labels)


def write_folder(folder: Path, note: str | None = None, style: str = STYLE) -> Path:
    edit = folder / "edit"
    (edit / "clip").mkdir(parents=True)
    a = [seg(0, ["thanh_pham"], 4.5), seg(10, ["nguoi_thao_tac"], 5.0), seg(20, ["khac"], 6.0)]
    b = [seg(0, ["thiet_bi"], 5.5), seg(10, ["thiet_bi", "nguoi_thao_tac"], 5.2)]
    scenes = [
        Scene(clip="a.mp4", index=0, start=0, end=30, sharpness=100, motion="tinh", jitter=0,
              consistency=1, segments={style: a}, label_probs={"chung_nhan": 0.02}),
        Scene(clip="b.mp4", index=0, start=0, end=30, sharpness=100, motion="tinh", jitter=0,
              consistency=1, segments={style: b}, label_probs={"chung_nhan": 0.10}),
    ]  # fmt: skip
    similar = [SimilarPair(clip="b.mp4", peak_a=2.0, peak_b=12.0, similarity=0.96, styles=[style])]
    sf = ScenesFile(folder=str(folder), rules_version="2.0", scenes=scenes,
                    usable_by_style={style: 4}, similar=similar)  # fmt: skip
    (edit / "scenes.json").write_text(sf.model_dump_json(), encoding="utf-8")
    for clip, vecs in (("a.mp4", [MEATBALL, MACHINE, MACHINE]), ("b.mp4", [MACHINE, MACHINE])):
        times = np.array([2.0, 12.0, 22.0][: len(vecs)])
        np.savez(edit / "clip" / f"{clip}.npz", key=np.array("k"), times=times, vecs=np.stack(vecs))
    opts: dict[str, Any] = {"phong_cach": style, "muc_dich": "gioi_thieu_xuong", "do_dai_giay": 15,
                            "so_ban_nhap": 1, "nhac": "khong_nhac"}  # fmt: skip
    if note is not None:
        opts["ghi_chu"] = note
    plan = compile_plan(VideoOptions(**opts), RULES, music=None, available_tags={"thiet_bi"})
    (edit / "plan.json").write_text(plan.model_dump_json(), encoding="utf-8")
    path = folder / "o.json"
    path.write_text(json.dumps(opts), encoding="utf-8")
    return path


def blocks(result: Any) -> dict[str, Any]:
    return {b.role: b for b in result.blocks}


def test_candidates_per_block_follow_the_spec_order(tmp_path: Path) -> None:
    opt = write_folder(tmp_path)
    result, out = build_pick(opt, tmp_path, RULES, GLOSSARY, embed_texts=fake_texts)
    assert out == tmp_path.resolve() / "edit" / "candidates.json"
    by = blocks(result)
    main_ids = [c.id for c in by["main"].candidates]
    assert "a_s0_2" not in main_ids  # "khac" segment is never a candidate
    # main requires nguoi_thao_tac (ve_sinh) + chung_nhan + thiet_bi (bang_chung): tagged ones only
    assert set(main_ids) == {"a_s0_1", "b_s0_0", "b_s0_1"}
    assert main_ids[0] == "b_s0_0"  # highest aesthetic among tagged
    assert by["main"].missing_tags == ["chung_nhan"]
    assert result.missing_shots == ["Giấy chứng nhận"]
    assert by["hook"].candidates[0].id == "b_s0_0"  # no tags: aesthetic only
    assert result.similar_pairs == [("b_s0_0", "b_s0_1")]
    assert result.dishes == []


def test_dish_in_the_note_moves_matching_segments_first(tmp_path: Path) -> None:
    opt = write_folder(tmp_path, note="Nhấn mạnh bò viên của GANI")
    result, _ = build_pick(opt, tmp_path, RULES, GLOSSARY, embed_texts=fake_texts)
    assert result.dishes == ["bò viên"]
    hook = blocks(result)["hook"].candidates
    assert hook[0].id == "a_s0_0" and hook[0].dishes == ["bò viên"]  # 4.5 aesthetic, but a match


def test_dish_is_matched_on_any_frame_inside_the_segment_not_only_the_peak(tmp_path: Path) -> None:
    # Found on real footage 29/09/2026: the action peak is often a hand or a running machine;
    # the meatballs show clearly a second later. a_s0_1 (10–15 s, peak 12 s) is a "machine"
    # frame at its peak but has a meatball frame at 14 s.
    opt = write_folder(tmp_path, note="bò viên")
    cache = tmp_path / "edit" / "clip" / "a.mp4.npz"
    np.savez(cache, key=np.array("k"), times=np.array([2.0, 12.0, 14.0, 22.0]),
             vecs=np.stack([MEATBALL, MACHINE, MEATBALL, MACHINE]))  # fmt: skip
    result, _ = build_pick(opt, tmp_path, RULES, GLOSSARY, embed_texts=fake_texts)
    matched = {c.id for b in result.blocks for c in b.candidates if c.dishes}
    assert matched == {"a_s0_0", "a_s0_1"}


def test_dish_text_vectors_are_cached(tmp_path: Path) -> None:
    opt = write_folder(tmp_path, note="bò viên")
    build_pick(opt, tmp_path, RULES, GLOSSARY, embed_texts=fake_texts)
    calls: list[int] = []
    build_pick(opt, tmp_path, RULES, GLOSSARY,
               embed_texts=lambda t: fake_texts(t, calls))  # fmt: skip
    assert calls == []


def test_no_dish_means_no_clip_model_is_needed(tmp_path: Path) -> None:
    def boom(texts: Sequence[str]) -> Vectors:
        raise AssertionError("CLIP must not load when the note names no dish")

    build_pick(write_folder(tmp_path, note="Video giới thiệu xưởng"), tmp_path, RULES, GLOSSARY,
               embed_texts=boom)  # fmt: skip


def test_plan_is_required_and_must_match_the_options(tmp_path: Path) -> None:
    opt = write_folder(tmp_path)
    (tmp_path / "edit" / "plan.json").unlink()
    with pytest.raises(ConfigError, match="plan"):
        build_pick(opt, tmp_path, RULES, GLOSSARY, embed_texts=fake_texts)
    other = write_folder(tmp_path / "x", style="sang_trong")
    (tmp_path / "x" / "o.json").write_text(other.read_text(encoding="utf-8").replace(
        "sang_trong", STYLE), encoding="utf-8")  # fmt: skip
    with pytest.raises(ConfigError, match="lại lệnh `plan`"):
        build_pick(tmp_path / "x" / "o.json", tmp_path / "x", RULES, GLOSSARY,
                   embed_texts=fake_texts)  # fmt: skip


def test_cli_pick(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    opt = write_folder(tmp_path)
    assert main(["pick", str(opt), str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "main: 3 ứng viên" in out and "Giấy chứng nhận" in out


def test_cli_pick_without_plan(tmp_path: Path) -> None:
    opt = write_folder(tmp_path)
    (tmp_path / "edit" / "plan.json").unlink()
    assert main(["pick", str(opt), str(tmp_path)]) == 2
