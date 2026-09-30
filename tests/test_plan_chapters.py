"""Unit tests: domain/plan.py — chapters of a 3-minute video (phase 7a, design GD7 §2).

A 180 s plan is hook + 3 chapters + CTA. Each chapter is cut into sub-blocks of about
`sub_block_beats` so the per-block checks stay tight; the chapter title is drawn over the first
beats of the chapter (no extra time, the beat grid does not move). The feature ships switched off
(`durations.180.enabled = false`) until the real acceptance run of phase 7e.
"""

from __future__ import annotations

import copy
import itertools
from typing import Any

import pytest

from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.plan import (
    MusicInfo,
    Plan,
    PlanError,
    compile_plan,
    split_exact,
)
from gani_dung_video.infrastructure.config_files import load_rules

SHIPPED = load_rules()
ALL_TAGS = {"nguoi_thao_tac", "thiet_bi", "chung_nhan", "thanh_pham", "mon_an", "nguoi_noi"}
ALLOWED = [4, 6]


def rules_on() -> Any:
    r = copy.deepcopy(dict(SHIPPED))
    r["durations"]["180"]["enabled"] = True
    return r


RULES = rules_on()


def opts(**kw: Any) -> VideoOptions:
    base: dict[str, Any] = {"phong_cach": "ke_chuyen_nghe", "muc_dich": "gioi_thieu_xuong",
                            "do_dai_giay": 180, "so_ban_nhap": 2, "nhac": "thu_vien",
                            "nhac_file": "am_ap_84.mp3"}  # fmt: skip
    return VideoOptions(**{**base, **kw})


def music(bpm: float = 84.0, dur: float = 200.0) -> MusicInfo:
    return MusicInfo(track="am_ap_84.mp3", bpm=bpm, first_beat_sec=0.5, duration_sec=dur,
                     source="library")  # fmt: skip


def plan180(**kw: Any) -> Plan:
    m = kw.pop("music", music())
    return compile_plan(opts(**kw), RULES, music=m, available_tags=ALL_TAGS)


def reachable(n: int, allowed: list[int]) -> bool:
    ok = [True] + [False] * n
    for i in range(1, n + 1):
        ok[i] = any(i >= a and ok[i - a] for a in allowed)
    return ok[n]


# ─────────────── switched off by default ───────────────


def test_shipped_rules_still_refuse_three_minutes() -> None:
    assert SHIPPED["durations"]["180"]["enabled"] is False
    with pytest.raises(PlanError) as e:
        compile_plan(opts(), SHIPPED, music=music(), available_tags=ALL_TAGS)
    assert e.value.code == "PLAN_CHAPTERS"


def test_speech_driven_styles_stay_refused_even_when_chapters_are_on() -> None:
    o = opts(phong_cach="ban_hang_co_loi", muc_dich="ban_hang")
    with pytest.raises(PlanError) as e:
        compile_plan(o, RULES, music=music(), available_tags=ALL_TAGS)
    assert e.value.code == "PLAN_CHAPTERS_SPEECH"
    assert "lời" in str(e.value)


# ─────────────── the real layout: Kể chuyện nghề × Giới thiệu xưởng, 84 BPM ───────────────


def test_chapters_follow_the_purpose_and_are_not_blocks_of_their_own() -> None:
    p = plan180()
    assert [c.id for c in p.chapters] == ["ve_sinh", "quy_trinh", "bang_chung"]
    assert p.blocks[0].role == "hook" and p.blocks[0].chapter is None
    assert p.blocks[-1].role == "cta" and p.blocks[-1].chapter is None
    assert p.blocks[-1].text == "Nhắn tin nhận báo giá sỉ"
    mid = p.blocks[1:-1]
    assert all(b.chapter is not None for b in mid)
    order = [c.id for c in p.chapters]
    chapter_ids = [str(b.chapter) for b in mid]
    assert chapter_ids == sorted(chapter_ids, key=order.index)  # sub-blocks stay in chapter order


def test_hook_and_cta_get_real_seconds_not_a_share_of_three_minutes() -> None:
    p = plan180()
    hook = p.blocks[0].beats * p.beat_sec
    cta = p.blocks[-1].beats * p.beat_sec
    assert 3.0 <= hook <= 6.5  # design: hook 4–6 s
    assert 10.0 <= cta <= 14.5  # design: CTA 10–14 s


def test_chapters_split_the_rest_about_45_75_40() -> None:
    p = plan180()
    secs = [c.beats * p.beat_sec for c in p.chapters]
    total = sum(secs)
    for got, want in zip(secs, (45, 75, 40), strict=True):
        assert got / total == pytest.approx(want / 160, abs=0.04)
    assert 155 <= total <= 168  # 180 minus the hook and the CTA


def test_the_plan_adds_up_and_chapters_are_contiguous() -> None:
    p = plan180()
    assert sum(b.beats for b in p.blocks) == p.planned_beats
    assert abs(p.planned_beats - p.target_beats) < max(ALLOWED)
    cursor = p.blocks[0].beats  # the hook comes first
    for c in p.chapters:
        assert c.start_beat == cursor
        assert c.beats == sum(b.beats for b in p.blocks if b.chapter == c.id)
        assert c.blocks == [b.role for b in p.blocks if b.chapter == c.id]
        cursor += c.beats
    assert cursor + p.blocks[-1].beats == p.planned_beats


def test_sub_blocks_are_about_twenty_beats_and_numbered() -> None:
    p = plan180()
    for c in p.chapters:
        subs = [b for b in p.blocks if b.chapter == c.id]
        assert [b.role for b in subs] == [f"{c.id}#{i}" for i in range(1, len(subs) + 1)]
        assert all(14 <= b.beats <= 30 for b in subs)
        assert len(subs) >= 2


def test_every_block_size_is_a_sum_of_allowed_scene_sizes() -> None:
    p = plan180()
    assert p.beats_per_scene_allowed == ALLOWED
    assert all(reachable(b.beats, ALLOWED) for b in p.blocks)


def test_chapter_title_takes_the_first_beats_without_moving_the_grid() -> None:
    p = plan180()
    assert all(c.title_beats == 2 for c in p.chapters)  # 1.5 s at 84 BPM ≈ 2 beats
    assert all(c.title_beats <= c.beats for c in p.chapters)
    # a slow track: 60 BPM = 1 s per beat, so 1.5 s is 2 beats as well
    slow = plan180(music=music(bpm=60.0, dur=200.0), phong_cach="sang_trong")
    assert {c.title_beats for c in slow.chapters} == {2}


def test_required_tags_follow_the_chapter_they_belong_to() -> None:
    p = plan180()
    by_chapter = {c.id: {tuple(b.required_tags) for b in p.blocks if b.chapter == c.id}
                  for c in p.chapters}  # fmt: skip
    assert by_chapter["ve_sinh"] == {("nguoi_thao_tac",)}
    assert by_chapter["quy_trinh"] == {()}
    assert by_chapter["bang_chung"] == {("chung_nhan", "thiet_bi")}
    assert p.blocks[0].required_tags == [] and p.blocks[-1].required_tags == []


def test_a_missing_required_tag_still_raises_x8() -> None:
    p = compile_plan(opts(), RULES, music=music(), available_tags={"thiet_bi"})
    assert any(w.startswith("X8:") for w in p.warnings)


def test_text_rules_count_per_chapter_not_per_block() -> None:
    p = plan180()
    assert "per_block" not in p.text_rules
    assert p.text_rules["per_chapter"] == [3, 5]
    assert p.text_rules["max_words"] == 8  # the style's word limit stays


def test_a_plan_written_before_chapters_still_loads() -> None:
    d = compile_plan(opts(do_dai_giay=30), SHIPPED, music=music(), available_tags=ALL_TAGS)
    raw = d.model_dump()
    raw.pop("chapters")
    for b in raw["blocks"]:
        b.pop("chapter")
    old = Plan.model_validate(raw)
    assert old.chapters == [] and all(b.chapter is None for b in old.blocks)


def test_short_videos_have_no_chapters() -> None:
    for seconds in (15, 30, 45, 60):
        p = compile_plan(opts(do_dai_giay=seconds), SHIPPED, music=music(), available_tags=ALL_TAGS)
        assert p.chapters == [] and all(b.chapter is None for b in p.blocks)


# ─────────────── Kể chuyện thương hiệu: no separate hook/CTA, the big block is cut in two ───────


def test_brand_story_cuts_the_people_block_into_two_chapters() -> None:
    p = plan180(muc_dich="ke_chuyen_thuong_hieu")
    assert [c.id for c in p.chapters] == ["con_nguoi_1", "con_nguoi_2", "gia_tri"]
    assert p.blocks[0].role == "mo" and p.blocks[-1].role == "logo"
    assert p.blocks[-1].text == "Theo dõi GANI"
    tags = {tuple(b.required_tags) for b in p.blocks if b.chapter and b.chapter.startswith("con")}
    assert tags == {("nguoi_noi",)}


# ─────────────── sweep: every combo chapters are allowed for ───────────────


MUSIC_STYLES = ["ke_chuyen_nghe", "review_mon_an", "sang_trong", "theo_mo_ta"]
PURPOSES = ["ban_hang", "gioi_thieu_xuong", "ke_chuyen_thuong_hieu"]


@pytest.mark.parametrize(
    ("style", "purpose", "bpm"),
    [(s, p, b) for s, p in itertools.product(MUSIC_STYLES, PURPOSES) for b in (62.0, 84.0, 105.0)],
)
def test_sweep_invariants_hold_for_every_music_style_and_purpose(
    style: str, purpose: str, bpm: float
) -> None:
    o = opts(phong_cach=style, muc_dich=purpose)
    p = compile_plan(o, RULES, music=music(bpm=bpm), available_tags=ALL_TAGS)
    allowed = p.beats_per_scene_allowed
    assert sum(b.beats for b in p.blocks) == p.planned_beats
    assert abs(p.planned_beats - p.target_beats) < max(allowed)
    assert all(reachable(b.beats, allowed) for b in p.blocks)
    assert 3 <= len(p.chapters) <= 4
    assert len({b.role for b in p.blocks}) == len(p.blocks)  # block roles are unique
    cursor = p.blocks[0].beats
    for c in p.chapters:
        assert c.start_beat == cursor and c.title_beats >= 1
        cursor += c.beats


def test_no_music_uses_the_virtual_grid_and_still_makes_chapters() -> None:
    p = compile_plan(opts(nhac="khong_nhac", nhac_file=None), RULES, music=None,
                     available_tags=ALL_TAGS)  # fmt: skip
    assert p.driver == "grid" and len(p.chapters) == 3
    assert sum(b.beats for b in p.blocks) == p.planned_beats


# ─────────────── split_exact ───────────────


def test_split_exact_keeps_the_total_and_the_parts_balanced() -> None:
    got = split_exact(104, 5, ALLOWED)  # 4/6-beat scenes only make even totals
    assert sum(got) == 104 and len(got) == 5
    assert max(got) - min(got) <= 4 and all(reachable(n, ALLOWED) for n in got)


def test_split_exact_uses_fewer_parts_when_the_total_cannot_be_cut_that_small() -> None:
    got = split_exact(12, 5, ALLOWED)  # 5 parts need at least 20 beats
    assert got == [4, 4, 4]


def test_split_exact_single_part_is_the_total() -> None:
    assert split_exact(18, 1, ALLOWED) == [18]


# ─────────────── the rules file ───────────────


def test_rules_layouts_match_the_purposes() -> None:
    cfg = SHIPPED["durations"]["180"]
    lo, hi = cfg["chapters"]
    for name, purpose in SHIPPED["purposes"].items():
        if purpose["max_duration"] < 180:
            assert name not in cfg["layouts"]
            continue
        lay = cfg["layouts"][name]
        roles = [r for r, _ in purpose["blocks"]]
        assert lay["hook"] in roles and lay["cta"] in roles
        assert lo <= len(lay["chapters"]) <= hi
        ids = [c[0] for c in lay["chapters"]]
        assert len(set(ids)) == len(ids)
        assert all(src in roles and w > 0 for _, src, w in lay["chapters"])


def test_dead_keys_of_the_old_180_entry_are_gone() -> None:
    cfg = SHIPPED["durations"]["180"]
    assert "max_segments_per_clip" not in cfg and "min_gap_sec" not in cfg
    assert SHIPPED["vision"]["max_segments_per_clip"] == 3  # the one live setting


def test_three_drafts_warning_now_mentions_the_output_limit() -> None:
    msg = SHIPPED["conflicts"]["X15"]["msg"]
    assert "2 bản" in msg and "cắt" in msg
