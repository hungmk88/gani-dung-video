"""Unit tests: domain/plan.py — block beat allocation and plan compilation (4a, spec §7.2)."""

from __future__ import annotations

from typing import Any

import pytest

from gani_dung_video.domain.options import KhuyenMai, VideoOptions
from gani_dung_video.domain.plan import (
    MusicInfo,
    PlanError,
    allocate_blocks,
    compile_plan,
    snap_size,
)
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()
ALL_TAGS = {"nguoi_thao_tac", "thiet_bi", "chung_nhan", "thanh_pham", "mon_an", "nguoi_noi"}


def opts(**kw: Any) -> VideoOptions:
    base: dict[str, Any] = {"phong_cach": "ke_chuyen_nghe", "muc_dich": "gioi_thieu_xuong",
                            "do_dai_giay": 30, "so_ban_nhap": 2, "nhac": "thu_vien",
                            "nhac_file": "am_ap_92.mp3"}  # fmt: skip
    return VideoOptions(**{**base, **kw})


def music(bpm: float = 92.0, dur: float = 200.0, first: float = 0.5) -> MusicInfo:
    return MusicInfo(track="am_ap_92.mp3", bpm=bpm, first_beat_sec=first, duration_sec=dur,
                     source="library")  # fmt: skip


# ─────────────── snap_size: round to what scenes can actually add up to ───────────────


def test_snap_size_spec_examples() -> None:
    assert snap_size(4.6, [4, 6]) == 4  # spec §7.2: 10% × 46 = 4.6 → 4
    assert snap_size(11.5, [4, 6]) == 12  # 25% × 46 = 11.5 → 12


def test_snap_size_uses_sums_of_allowed_values_and_a_minimum_of_one_scene() -> None:
    assert snap_size(10.0, [6, 8]) == 12  # 10 = neither 6, 8 nor a sum of them; 8 and 12 tie → up
    assert snap_size(0.2, [4, 6]) == 4  # a block always keeps at least one scene
    assert snap_size(5.0, [4, 6]) == 6  # tie between 4 and 6 rounds up
    assert snap_size(7.4, [1, 2]) == 7


# ─────────────── allocate_blocks ───────────────


def test_allocation_spec_example_46_beats_dumps_remainder_in_longest_content_block() -> None:
    ratios = [("hook", 0.10), ("ve_sinh", 0.25), ("quy_trinh", 0.35), ("bang_chung", 0.15),
              ("cta", 0.15)]  # fmt: skip
    got = allocate_blocks(ratios, 46, [4, 6])
    # 4.6→4, 11.5→12, 16.1→16, 6.9→6, 6.9→6 = 44; the 2 left over go to "quy_trinh" (16→18).
    assert got == [("hook", 4), ("ve_sinh", 12), ("quy_trinh", 18), ("bang_chung", 6), ("cta", 6)]
    assert sum(b for _, b in got) == 46


def test_allocation_over_budget_takes_beats_back_from_the_longest_content_block() -> None:
    ratios = [("hook", 0.10), ("nhu_cau", 0.15), ("san_pham", 0.45), ("bang_chung", 0.15),
              ("cta", 0.15)]  # fmt: skip
    got = allocate_blocks(ratios, 32, [6, 8])  # minimum 6 per block = 36 > 32
    assert dict(got)["san_pham"] == 8 and sum(b for _, b in got) == 32


def test_remainder_moves_to_another_content_block_when_the_longest_cannot_take_it() -> None:
    # Found on real footage (Sang trọng 30 s, 72 BPM): 36 beats, blocks rounded to 6/8/12/6/6
    # = 38; the longest block (12) cannot lose exactly 2 (10 is not a 6/8 sum), but the
    # 8-beat block can (→ 6), so the plan must hit 36 exactly.
    ratios = [("hook", 0.10), ("ve_sinh", 0.25), ("quy_trinh", 0.35), ("bang_chung", 0.15),
              ("cta", 0.15)]  # fmt: skip
    got = allocate_blocks(ratios, 36, [6, 8])
    assert sum(b for _, b in got) == 36
    assert dict(got)["hook"] == 6 and dict(got)["cta"] == 6  # hook/cta untouched


def test_allocation_that_cannot_hit_the_total_stays_within_one_scene() -> None:
    ratios = [("hook", 0.10), ("ve_sinh", 0.25), ("quy_trinh", 0.35), ("bang_chung", 0.15),
              ("cta", 0.15)]  # fmt: skip
    total = sum(b for _, b in allocate_blocks(ratios, 45, [4, 6]))  # 45 is odd, scenes are even
    assert total in (44, 46)


# ─────────────── compile_plan ───────────────


def test_plan_for_music_driven_style_matches_spec_example() -> None:
    p = compile_plan(opts(), RULES, music=music(), available_tags=ALL_TAGS)
    assert p.driver == "music" and p.target_beats == 46
    assert p.beat_sec == pytest.approx(60 / 92)
    assert p.beats_per_scene_allowed == [4, 6]
    assert [(b.role, b.beats) for b in p.blocks] == [("hook", 4), ("ve_sinh", 12),
                                                     ("quy_trinh", 18), ("bang_chung", 6),
                                                     ("cta", 6)]  # fmt: skip
    by = {b.role: b for b in p.blocks}
    assert by["ve_sinh"].required_tags == ["nguoi_thao_tac"]
    assert by["bang_chung"].required_tags == ["chung_nhan", "thiet_bi"]
    assert by["cta"].text == "Nhắn tin nhận báo giá sỉ"
    assert p.planned_beats == 46 and p.warnings == []
    assert p.draft_strategy == ["toi_uu", "doi_hook_doi_mach"]
    assert p.text_rules["max_words"] == 8 and p.text_rules["per_block"] == 1
    assert p.music is not None and p.music.audio_in_sec == 0.5 and p.music.fold == "giu_nguyen"


def test_missing_required_tags_become_an_x8_warning() -> None:
    p = compile_plan(opts(), RULES, music=music(), available_tags={"nguoi_thao_tac", "thiet_bi"})
    assert len(p.warnings) == 1 and p.warnings[0].startswith("X8") and "chung_nhan" in p.warnings[0]


def test_track_outside_style_range_is_folded_and_beats_follow_the_folded_tempo() -> None:
    o = opts(phong_cach="sang_trong", muc_dich="ban_hang")
    p = compile_plan(o, RULES, music=music(bpm=128.0), available_tags=ALL_TAGS)
    assert p.music is not None and p.music.fold == "dem_2_thanh_1"
    assert p.music.effective_bpm == 64.0 and p.beat_sec == pytest.approx(0.9375)
    assert p.target_beats == 32 and p.planned_beats == 32
    assert p.beats_per_scene_allowed == [6, 8]


def test_no_music_uses_the_virtual_grid_of_the_style() -> None:
    o = opts(phong_cach="tvc_cat_nhanh", muc_dich="ban_hang", nhac="khong_nhac", nhac_file=None)
    p = compile_plan(o, RULES, music=None, available_tags=ALL_TAGS)
    # The grid beat (1.3 s) is stretched so the 23 planned beats add up to exactly 30 s.
    assert p.driver == "grid" and p.beat_sec == pytest.approx(30 / 23) and p.music is None
    assert p.target_beats == 23 and p.planned_beats * p.beat_sec == pytest.approx(30.0)
    assert [(b.role, b.beats) for b in p.blocks] == [("hook", 2), ("nhu_cau", 3), ("san_pham", 12),
                                                     ("bang_chung", 3), ("cta", 3)]  # fmt: skip
    assert p.warnings == []  # X11 is already reported by preflight for TVC


def test_no_music_on_a_style_without_grid_fallback_warns() -> None:
    # The shortest allowed scene (4 beats) lasts the style's average scene (3.0 s): beat 0.75 s.
    o = opts(nhac="khong_nhac", nhac_file=None)
    p = compile_plan(o, RULES, music=None, available_tags=ALL_TAGS)
    assert p.driver == "grid" and p.beat_sec == pytest.approx(0.75) and p.target_beats == 40
    assert p.planned_beats == 40
    assert any(w.startswith("W_GRID_NO_MUSIC") for w in p.warnings)


def test_music_that_could_not_be_analysed_falls_back_to_the_grid_with_a_warning() -> None:
    p = compile_plan(opts(), RULES, music=None, available_tags=ALL_TAGS)
    assert p.driver == "grid" and any(w.startswith("W_NO_BEAT") for w in p.warnings)


def test_15_second_video_merges_blocks_into_hook_main_cta() -> None:
    o = opts(phong_cach="tvc_cat_nhanh", muc_dich="ban_hang", do_dai_giay=15, nhac="khong_nhac",
             nhac_file=None)  # fmt: skip
    p = compile_plan(o, RULES, music=None, available_tags=ALL_TAGS)
    assert [b.role for b in p.blocks] == ["hook", "main", "cta"]
    assert p.target_beats == 12
    assert [b.beats for b in p.blocks] == [1, 9, 2]  # hook ≤ 2 s = 1 beat, cta 3 s = 2 beats
    assert p.blocks[1].required_tags == ["thanh_pham", "mon_an"]


def test_short_track_gets_loop_info() -> None:
    p = compile_plan(opts(), RULES, music=music(dur=20.0), available_tags=ALL_TAGS)
    assert p.music is not None and p.music.repeats == 2 and p.music.loop_sec is not None


# ─────────────── audit 4: the music must cover the end card too ───────────────


def test_a_track_as_long_as_the_chosen_length_still_loops_for_the_end_card() -> None:
    """15 s of music for a 15 s video: the render is 15 s + the 2 s end card long, and the music
    is cut at the end of the video, so it used to end 2 s early with no fade."""
    p = compile_plan(opts(do_dai_giay=15), RULES, music=music(dur=15.0, first=0.0),
                     available_tags=ALL_TAGS)  # fmt: skip
    assert p.music is not None and p.music.loop_sec is not None and p.music.repeats is not None
    assert p.music.loop_sec * p.music.repeats >= 15 + RULES["render"]["end_card_sec"] - 0.01


def test_the_first_beat_is_not_music_that_can_be_heard() -> None:
    # 17.5 s track, music starts at 1.0 s: 16.5 s heard, the video needs 15 + 2 = 17 s
    p = compile_plan(opts(do_dai_giay=15), RULES, music=music(dur=17.5, first=1.0),
                     available_tags=ALL_TAGS)  # fmt: skip
    assert p.music is not None and p.music.loop_sec is not None


def test_a_track_that_covers_video_and_end_card_does_not_loop() -> None:
    p = compile_plan(opts(do_dai_giay=15), RULES, music=music(dur=19.0, first=1.0),
                     available_tags=ALL_TAGS)  # fmt: skip
    assert p.music is not None and p.music.loop_sec is None and p.music.repeats is None


def test_the_loop_is_sized_for_the_length_actually_rendered() -> None:
    """Long enough for the chosen length plus end card but not for a plan that runs longer
    (here the natural plan is trimmed back to the chosen length, so it still holds)."""
    p = compile_plan(opts(do_dai_giay=30), RULES, music=music(bpm=92.0, dur=200.0),
                     available_tags=ALL_TAGS)  # fmt: skip
    assert p.music is not None and p.music.loop_sec is None


def test_promo_cta_text_gets_the_deadline() -> None:
    o = opts(phong_cach="review_mon_an", muc_dich="khuyen_mai", do_dai_giay=30,
             khuyen_mai=KhuyenMai(uu_dai="Giảm 20%", thoi_han="30/10"))  # fmt: skip
    p = compile_plan(o, RULES, music=music(bpm=100.0), available_tags=ALL_TAGS)
    assert p.blocks[-1].text == "Ưu đãi đến 30/10"


def test_speech_style_compiles_with_the_speech_driver_and_broll_beats() -> None:
    # Phase 5c-1: speech decides the cuts; the beat grid only places B-roll ({2, 4} beats).
    o = opts(phong_cach="ban_hang_co_loi", muc_dich="ban_hang")
    p = compile_plan(o, RULES, music=music(bpm=100.0), available_tags=ALL_TAGS)
    assert p.driver == "speech" and p.beats_per_scene_allowed == [2, 4]
    assert p.beat_sec == pytest.approx(0.6) and p.music is not None
    assert [b.role for b in p.blocks] == ["hook", "nhu_cau", "san_pham", "bang_chung", "cta"]
    assert p.blocks[-1].text == "Nhắn tin đặt hàng"


def test_speech_style_without_music_uses_a_grid_for_broll() -> None:
    o = opts(phong_cach="ban_hang_co_loi", muc_dich="ban_hang", nhac="khong_nhac", nhac_file=None)
    p = compile_plan(o, RULES, music=None, available_tags=ALL_TAGS)
    assert p.driver == "speech" and p.music is None and p.beat_sec == pytest.approx(1.25)


def test_three_minute_video_is_not_supported_yet() -> None:
    with pytest.raises(PlanError) as e:
        compile_plan(opts(do_dai_giay=180), RULES, music=music(), available_tags=ALL_TAGS)
    assert e.value.code == "PLAN_CHAPTERS"


def test_plan_serialises_to_json_with_the_spec_keys() -> None:
    d = compile_plan(opts(), RULES, music=music(), available_tags=ALL_TAGS).model_dump()
    assert {"options", "driver", "music", "target_beats", "beats_per_scene_allowed", "blocks",
            "text_rules", "draft_strategy", "warnings"} <= set(d)  # fmt: skip
