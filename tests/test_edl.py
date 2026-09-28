"""Unit tests: domain/edl.py — drafts.json → edit decision list for ffmpeg (5a, spec §7.5)."""

from __future__ import annotations

from typing import Any

import pytest

from gani_dung_video.domain.beats import SnappedSegment
from gani_dung_video.domain.drafts import SnappedDraft
from gani_dung_video.domain.edl import EdlError, build_edl
from gani_dung_video.domain.footage import Clip
from gani_dung_video.domain.plan import Plan, PlanBlock, PlanMusic
from gani_dung_video.infrastructure.config_files import load_rules

RULES = load_rules()
FPS = 30


def clip(name: str, dur: float = 60.0, hdr: bool = False, audio: bool = True) -> Clip:
    return Clip(file=name, size_bytes=1, duration_sec=dur, width=1080, height=1920, rotation=0,
                orientation="doc", fps=30.0, video_codec="h264", pix_fmt="yuv420p",
                color_transfer=None, color_primaries=None, is_hdr=hdr, has_audio_stream=audio,
                audio_tracks=1 if audio else 0)  # fmt: skip


CLIPS = {c.file: c for c in (clip("a.mp4"), clip("b.mp4", hdr=True), clip("c.mp4", dur=10.0))}


def cut(
    block: str, source: str, t0: float, t1: float, src_in: float, text: str = ""
) -> SnappedSegment:
    return SnappedSegment(block=block, scene_id=f"{source[:-4]}_s0_0", beats=4, text=text,
                          t_start=t0, t_end=t1, src_in=src_in, src_out=src_in + (t1 - t0),
                          peak_ratio=0.35, clip=source)  # fmt: skip


def draft(segs: list[SnappedSegment]) -> SnappedDraft:
    return SnappedDraft(draft="A", label="x", segments=segs, missing_shots=[], warnings=[])


def plan(style: str = "ke_chuyen_nghe", music: PlanMusic | None = None,
         text_rules: dict[str, Any] | None = None) -> Plan:  # fmt: skip
    return Plan(rules_version="2.0",
                options={"style": style, "purpose": "gioi_thieu_xuong", "duration": 15,
                         "drafts": 1, "music": "thu_vien" if music else "khong_nhac"},
                driver="music" if music else "grid", music=music, beat_sec=0.75, target_beats=20,
                planned_beats=20, beats_per_scene_allowed=[4, 6],
                blocks=[PlanBlock(role="hook", beats=4), PlanBlock(role="main", beats=12),
                        PlanBlock(role="cta", beats=4, text="Nhắn tin nhận báo giá sỉ")],
                text_rules=text_rules or {"max_words": 8, "per_block": 1},
                draft_strategy=["toi_uu"])  # fmt: skip


SEGS = [
    cut("hook", "a.mp4", 0.0, 3.0, 5.0, "Một ngày ở xưởng"),
    cut("main", "b.mp4", 3.0, 6.0, 1.0, "Tay nghề từng mẻ"),
    cut("main", "a.mp4", 6.0, 9.0, 20.0),
    cut("main", "c.mp4", 9.0, 12.0, 6.8),  # c.mp4 is 10 s long: only 0.2 s left after src_out
    cut("cta", "a.mp4", 12.0, 15.0, 40.0, "Nhắn tin nhận báo giá sỉ"),
]


def frames(t: float) -> float:
    return t * FPS


# ─────────────── timeline ───────────────


def test_shots_follow_the_snapped_timeline_and_end_card_follows() -> None:
    e = build_edl(draft(SEGS), plan(), CLIPS, RULES, preview=False)
    assert [(s.source, s.t_start, s.t_end) for s in e.shots] == [
        ("a.mp4", 0.0, 3.0), ("b.mp4", 3.0, 6.0), ("a.mp4", 6.0, 9.0), ("c.mp4", 9.0, 12.0),
        ("a.mp4", 12.0, 15.0)]  # fmt: skip
    assert e.content_end == 15.0 and e.end_card_sec == 2.0 and e.duration == 17.0
    assert (e.width, e.height, e.fps) == (1080, 1920, 30)


def test_preview_is_540p_and_lighter() -> None:
    final = build_edl(draft(SEGS), plan(), CLIPS, RULES, preview=False)
    prev = build_edl(draft(SEGS), plan(), CLIPS, RULES, preview=True)
    assert (prev.width, prev.height) == (540, 960) and prev.video_kbps < final.video_kbps


def test_every_time_is_on_a_frame() -> None:
    e = build_edl(draft(SEGS), plan("sang_trong"), CLIPS, RULES, preview=False)
    for s in e.shots:
        for t in (s.t_start, s.t_end, s.src_in, s.src_out, s.dissolve_out_sec):
            assert frames(t) == pytest.approx(round(frames(t)), abs=1e-6)


def test_hdr_and_audio_facts_come_from_the_inventory() -> None:
    e = build_edl(draft(SEGS), plan(), CLIPS, RULES, preview=False)
    assert [s.hdr for s in e.shots] == [False, True, False, False, False]


def test_a_clip_missing_from_the_inventory_is_refused() -> None:
    bad = [*SEGS[:1], cut("main", "zz.mp4", 3.0, 6.0, 0.0)]
    with pytest.raises(EdlError, match=r"zz\.mp4"):
        build_edl(draft(bad), plan(), CLIPS, RULES, preview=False)


# ─────────────── transitions: cuts stay on the beat ───────────────


def test_tvc_is_all_cuts() -> None:
    e = build_edl(draft(SEGS), plan("tvc_cat_nhanh", text_rules={"max_words": 4}), CLIPS, RULES,
                  preview=False)  # fmt: skip
    assert all(s.dissolve_out_sec == 0 for s in e.shots)


def test_sang_trong_dissolves_everywhere_by_extending_the_outgoing_shot() -> None:
    e = build_edl(draft(SEGS), plan("sang_trong"), CLIPS, RULES, preview=False)
    d = 12 / FPS  # dissolve_frames = 12
    assert [s.dissolve_out_sec for s in e.shots] == pytest.approx([d, d, d, d, 0.0])
    a0 = e.shots[0]
    assert a0.src_out == pytest.approx(5.0 + 3.0 + d)  # 0.4 s more source: the cut stays at 3.0
    assert a0.t_end == 3.0


def test_ke_chuyen_nghe_dissolves_only_between_blocks() -> None:
    e = build_edl(draft(SEGS), plan("ke_chuyen_nghe"), CLIPS, RULES, preview=False)
    d = 7 / FPS
    assert [s.dissolve_out_sec for s in e.shots] == pytest.approx([d, 0.0, 0.0, d, 0.0])


def test_extension_past_the_end_of_the_source_is_padded_with_its_last_frame() -> None:
    e = build_edl(draft(SEGS), plan("sang_trong"), CLIPS, RULES, preview=False)
    c = e.shots[3]  # c.mp4: 10 s long, src_out 9.8 + 0.4 s wanted
    assert c.src_out == pytest.approx(10.0) and c.pad_sec == pytest.approx(0.2)
    assert all(s.pad_sec == 0 for s in e.shots if s.source != "c.mp4")


# ─────────────── audio ───────────────


def test_music_starts_on_its_first_beat_and_loops_whole_bars() -> None:
    m = PlanMusic(track="t.m4a", bpm=80.0, effective_bpm=80.0, fold="giu_nguyen",
                  first_beat_sec=0.5, audio_in_sec=0.5, duration_sec=12.0, source="library",
                  loop_sec=9.0, repeats=2)  # fmt: skip
    e = build_edl(draft(SEGS), plan(music=m), CLIPS, RULES, preview=False)
    assert e.music is not None and (e.music.track, e.music.audio_in_sec) == ("t.m4a", 0.5)
    assert (e.music.loop_sec, e.music.fade_out_sec) == (9.0, RULES["render"]["music_fade_sec"])
    assert build_edl(draft(SEGS), plan(), CLIPS, RULES, preview=False).music is None


def test_ambient_sound_level_follows_the_style() -> None:
    assert build_edl(draft(SEGS), plan("ke_chuyen_nghe"), CLIPS, RULES, False).ambient_db == -20
    assert build_edl(draft(SEGS), plan("review_mon_an"), CLIPS, RULES, False).ambient_db == -12
    tvc = plan("tvc_cat_nhanh", text_rules={"max_words": 4})
    assert build_edl(draft(SEGS), tvc, CLIPS, RULES, False).ambient_db is None  # muted
    e = build_edl(draft(SEGS), plan(), CLIPS, RULES, preview=False)
    assert (e.lufs, e.true_peak_db) == (-14.0, RULES["render"]["true_peak_db"])


# ─────────────── captions ───────────────


def test_one_line_per_block_stays_up_for_the_whole_block() -> None:
    e = build_edl(draft(SEGS), plan("ke_chuyen_nghe"), CLIPS, RULES, preview=False)
    assert [(c.start, c.end, c.text, c.kind) for c in e.captions] == [
        (0.0, 3.0, "Một ngày ở xưởng", "text"),
        (3.0, 12.0, "Tay nghề từng mẻ", "text"),  # main block runs 3–12 s
        (12.0, 15.0, "Nhắn tin nhận báo giá sỉ", "cta"),
    ]


def test_tvc_captions_pop_for_their_own_shot_only() -> None:
    e = build_edl(draft(SEGS), plan("tvc_cat_nhanh", text_rules={"max_words": 4,
                  "every_n_scenes": 2}), CLIPS, RULES, preview=False)  # fmt: skip
    assert [(c.start, c.end) for c in e.captions] == [(0.0, 3.0), (3.0, 6.0), (12.0, 15.0)]


def test_font_follows_the_style() -> None:
    assert build_edl(draft(SEGS), plan("sang_trong"), CLIPS, RULES, False).font == (
        "Be Vietnam Pro Light")  # fmt: skip
    assert build_edl(draft(SEGS), plan("review_mon_an"), CLIPS, RULES, False).font == (
        "Be Vietnam Pro ExtraBold")  # fmt: skip


# ─────────────── speech mode (5c-1) ───────────────


def speech_plan(music: PlanMusic | None = None) -> Plan:
    p = plan("ban_hang_co_loi", music=music)
    return p.model_copy(update={"driver": "speech", "beats_per_scene_allowed": [2, 4],
                                "options": {**p.options, "style": "ban_hang_co_loi",
                                            "purpose": "ban_hang"}})  # fmt: skip


def speech_cuts() -> list[Any]:
    from gani_dung_video.domain.speech import SpokenWord
    from gani_dung_video.domain.speech_edit import SpeechCut

    def w(text: str, a: float, b: float) -> SpokenWord:
        return SpokenWord(text=text, start=a, end=b, prob=0.9)

    return [
        SpeechCut(phrase_id="s_p0", clip="a.mp4", src_in=10.0, src_out=12.0, t_start=0.0,
                  t_end=2.0, words=[w("Giò", 0.1, 0.5), w("lụa", 0.5, 0.9), w("ngon.", 0.9, 1.8)]),
        SpeechCut(phrase_id="s_p3", clip="a.mp4", src_in=30.0, src_out=33.0, t_start=2.0,
                  t_end=5.0, words=[w("Nhắn", 2.1, 2.6), w("tin", 2.6, 3.0), w("ngay", 3.0, 4.8)]),
    ]  # fmt: skip


def test_speech_edl_keeps_the_voice_ducks_the_music_and_places_broll() -> None:
    from gani_dung_video.domain.edl import BrollPlacement, build_speech_edl
    from gani_dung_video.domain.speech_edit import Overlay

    m = PlanMusic(track="t.m4a", bpm=100.0, effective_bpm=100.0, fold="giu_nguyen",
                  first_beat_sec=0.2, audio_in_sec=0.2, duration_sec=60.0,
                  source="library")  # fmt: skip
    ov = BrollPlacement(overlay=Overlay(phrase_id="s_p3", t_start=2.4, t_end=3.6, beats=2),
                        clip="b.mp4", start=5.0, end=10.0, peak=6.0)  # fmt: skip
    e = build_speech_edl(speech_cuts(), [ov], speech_plan(m), CLIPS, RULES, preview=True)
    assert e.mode == "speech" and e.ambient_db == 0.0  # the voice stays at full level
    assert [(s.source, s.t_start, s.t_end) for s in e.shots] == [("a.mp4", 0.0, 2.0),
                                                                 ("a.mp4", 2.0, 5.0)]  # fmt: skip
    [o] = e.overlays
    assert (o.source, o.t_start, o.t_end) == ("b.mp4", 2.4, 3.6)
    assert o.src_in == pytest.approx(6.0 - 0.35 * 1.2, abs=1 / FPS) and o.hdr is True  # b: HDR
    # 0.3 s between the phrases < duck_merge_gap_sec 0.4: one span, no pumping in the gap
    assert e.duck_db == -18 and e.duck == [(0.1, 4.8)]
    assert e.content_end == 5.0 and e.duration == 7.0
    assert [(c.text, c.kind) for c in e.captions] == [("Nhắn tin nhận báo giá sỉ", "cta")]
    assert [[w.text for w in g] for g in e.karaoke] == [["Giò", "lụa", "ngon."],
                                                        ["Nhắn", "tin", "ngay"]]  # fmt: skip


def test_speech_edl_refuses_broll_from_an_unknown_clip() -> None:
    from gani_dung_video.domain.edl import BrollPlacement, build_speech_edl
    from gani_dung_video.domain.speech_edit import Overlay

    ov = BrollPlacement(overlay=Overlay(phrase_id="s_p3", t_start=2.4, t_end=3.6, beats=2),
                        clip="zz.mp4", start=0, end=5, peak=1)  # fmt: skip
    with pytest.raises(EdlError, match="zz"):
        build_speech_edl(speech_cuts(), [ov], speech_plan(), CLIPS, RULES, preview=True)
