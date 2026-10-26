"""Unit tests: domain/edl.py — drafts.json → edit decision list for ffmpeg (5a, spec §7.5)."""

from __future__ import annotations

import random
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
    # no dissolves; the one overlap is the hook's glitch (transitions on for TVC, 26/10/2026)
    assert all(s.dissolve_out_sec == 0 or s.transition == "pixelize" for s in e.shots)


def test_tvc_punches_in_on_the_beat_and_other_styles_do_not() -> None:
    # 26/10/2026, domain/zoom.py: TVC shots of ≥ 2 beats punch in 5 % on their second beat,
    # at most max_effects (3) of them; c.mp4 etc. are 1080×1920 so the zoom fits.
    e = build_edl(draft(SEGS), plan("tvc_cat_nhanh", text_rules={"max_words": 4}), CLIPS, RULES,
                  preview=True)  # fmt: skip
    punched = [i for i, s in enumerate(e.shots) if s.punch_sec is not None]
    assert punched == [0, 2, 4]
    assert all(e.shots[i].punch_sec == 0.75 and e.shots[i].punch_zoom == pytest.approx(1.05)
               and e.shots[i].zoom == 1.0 for i in punched)  # fmt: skip
    calm = build_edl(draft(SEGS), plan(), CLIPS, RULES, preview=True)
    assert all(s.punch_sec is None and s.zoom == 1.0 for s in calm.shots)


def test_review_plays_clips_filmed_at_60_fps_slowly_and_silently_on_the_same_timeline() -> None:
    fast = {**CLIPS, "f.mp4": clip("f.mp4").model_copy(update={"fps": 60.0})}
    segs = [cut("hook", "f.mp4", 0.0, 3.0, 5.0), cut("main", "a.mp4", 3.0, 6.0, 1.0),
            cut("main", "f.mp4", 6.0, 9.0, 20.0), cut("cta", "f.mp4", 9.0, 12.0, 40.0)]  # fmt: skip
    e = build_edl(draft(segs), plan("review_mon_an"), fast, RULES, preview=True)
    assert [s.speed for s in e.shots] == [0.5, 1.0, 0.5, 0.5]  # a.mp4 is 30 fps
    first = e.shots[0]
    assert (first.t_start, first.t_end) == (0.0, 3.0)  # the cut points never move
    assert first.src_out - first.src_in == pytest.approx(1.5)  # half the clip, played over 3 s
    assert first.src_in == pytest.approx(5.0 + 0.35 * 1.5, abs=1 / FPS)  # around the same peak
    assert all(s.speed == 1.0 for s in build_edl(draft(segs), plan(), fast, RULES, True).shots)


def with_transitions(*styles: str) -> Any:
    on = list(styles) or list(RULES["transitions"]["styles"])
    return {**RULES, "transitions": {**RULES["transitions"], "enabled_styles": on}}


def test_extra_transitions_are_on_for_tvc_and_review_only() -> None:
    # 26/10/2026: the owner switched them on for TVC cắt nhanh and Review món ăn
    assert RULES["transitions"]["enabled_styles"] == ["tvc_cat_nhanh", "review_mon_an"]
    tvc = build_edl(draft(SEGS), plan("tvc_cat_nhanh", text_rules={"max_words": 4}), CLIPS,
                    RULES, preview=True)  # fmt: skip
    assert tvc.end_wipe == "circleclose"
    e = build_edl(draft(SEGS), plan(), CLIPS, RULES, preview=True)  # Kể chuyện nghề: still off
    assert all(s.transition == "fade" for s in e.shots)
    assert e.end_wipe is None and e.light_leaks == []


def test_tvc_glitches_out_of_the_hook_and_closes_on_an_iris() -> None:
    e = build_edl(draft(SEGS), plan("tvc_cat_nhanh", text_rules={"max_words": 4}), CLIPS,
                  with_transitions(), preview=True)  # fmt: skip
    hook = e.shots[0]  # the only hook shot: its cut into "main" is the one that glitches
    assert hook.transition == "pixelize" and round(hook.dissolve_out_sec * FPS) == 8
    assert hook.src_out - hook.src_in == pytest.approx(3.0 + 8 / FPS)  # extended like a dissolve
    assert hook.t_end == 3.0  # the cut stays on its beat
    assert all(s.dissolve_out_sec == 0 for s in e.shots[1:])
    assert e.end_wipe == "circleclose" and round(e.end_wipe_sec * FPS) == 15


def test_storytelling_whips_between_process_steps_only() -> None:
    segs = [
        cut("hook", "a.mp4", 0.0, 3.0, 5.0),
        cut("quy_trinh", "a.mp4", 3.0, 6.0, 10.0),
        cut("quy_trinh", "a.mp4", 6.0, 9.0, 20.0),
        cut("cta", "a.mp4", 9.0, 12.0, 30.0),
    ]
    e = build_edl(draft(segs), plan(), CLIPS, with_transitions(), preview=True)
    # hook→quy_trinh and quy_trinh→cta keep the style's block dissolve (7 frames)
    assert [(s.transition, round(s.dissolve_out_sec * FPS)) for s in e.shots] == [
        ("fade", 7),
        ("slideleft", 6),
        ("fade", 7),
        ("fade", 0),
    ]


def test_only_the_styles_switched_on_get_their_transitions() -> None:
    tvc = plan("tvc_cat_nhanh", text_rules={"max_words": 4})
    off = build_edl(draft(SEGS), tvc, CLIPS, with_transitions("review_mon_an"), preview=True)
    assert off.end_wipe is None and all(s.transition == "fade" for s in off.shots)
    on = build_edl(draft(SEGS), tvc, CLIPS, with_transitions("tvc_cat_nhanh"), preview=True)
    assert on.end_wipe == "circleclose"


def test_luxury_fades_to_black_before_the_end_card() -> None:
    # spec v2.1: sang_trong "end": "fade_black", over its own dissolve (12 frames)
    e = build_edl(draft(SEGS), plan("sang_trong"), CLIPS, RULES, preview=True)
    assert e.end_wipe == "fade" and round(e.end_wipe_sec * FPS) == 12
    assert build_edl(draft(SEGS), plan(), CLIPS, RULES, preview=True).end_wipe is None
    lit = build_edl(draft(SEGS), plan("sang_trong"), CLIPS, with_transitions(), preview=True)
    assert lit.end_wipe == "circleclose"  # an extra end transition, when on, takes its place


def test_luxury_sweeps_light_at_each_block_change() -> None:
    e = build_edl(draft(SEGS), plan("sang_trong"), CLIPS, with_transitions(), preview=True)
    assert e.light_leaks == pytest.approx([3.0 - 0.8, 12.0 - 0.8])  # hook→main, main→cta
    assert (e.light_leak_sec, e.light_leak_opacity) == (1.6, 0.35) and e.light_leak_colors


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


def test_the_cut_out_never_reads_past_the_end_of_the_source() -> None:
    clips = {"d.mp4": clip("d.mp4", dur=3.02)}  # 90.6 frames: frame 91 does not exist
    e = build_edl(draft([cut("hook", "d.mp4", 0.0, 3.0, 0.1)]), plan(), clips, RULES, False)
    [s] = e.shots
    assert s.src_out <= 3.02 and frames(s.src_out) == pytest.approx(90)
    assert frames(s.pad_sec) == pytest.approx(round(frames(0.1 + 3.0)) - 90)  # from rounded ends


def _played_frames(s: Any) -> int:
    """Frames a shot adds to the joined picture: its source window, its freeze, minus the
    cross-fade the next shot overlaps it by."""
    return round(frames(s.src_out - s.src_in)) + round(frames(s.pad_sec)) - round(
        frames(s.dissolve_out_sec))  # fmt: skip


@pytest.mark.parametrize("style", ["tvc_cat_nhanh", "ke_chuyen_nghe", "sang_trong"])
def test_shots_add_up_to_the_planned_timeline_to_the_frame(style: str) -> None:
    rng = random.Random(f"edl-{style}")
    for _ in range(200):
        beat = rng.uniform(0.3, 1.2)
        n = rng.randint(2, 8)
        bounds = sorted(rng.sample(range(1, 4 * n), n - 1))
        edges = [round(b * beat * FPS) / FPS for b in [0, *bounds, 4 * n]]
        blocks = ["hook"] + ["main"] * (n - 2) + ["cta"]
        clips: dict[str, Any] = {}
        segs = []
        for i in range(n):
            src_in = rng.uniform(0.0, 5.0)
            length = edges[i + 1] - edges[i]
            name = f"v{i}.mp4"  # some sources end before the cut (and its dissolve) does
            clips[name] = clip(name, dur=src_in + length + rng.uniform(-0.3, 1.0))
            segs.append(cut(blocks[i], name, edges[i], edges[i + 1], src_in))
        p = plan(style, text_rules={"max_words": 4})
        if rng.random() < 0.3:  # music mode cut at a chosen length
            p = trimmed(round(rng.uniform(edges[1], edges[-1]), 1), fade=0.0)
        e = build_edl(draft(segs), p, clips, RULES, preview=False)
        played = 0
        for s in e.shots:
            played += _played_frames(s)
            assert played == round(frames(s.t_end))  # 0 frames of drift at every cut
            assert s.src_out <= clips[s.source].duration_sec + 1e-9
            assert s.pad_sec >= 0
        assert played == round(frames(e.content_end))


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


def test_finishing_filters_are_off_by_default_and_follow_the_rules_when_on() -> None:
    off = build_edl(draft(SEGS), plan(), CLIPS, RULES, preview=False)
    assert off.denoise is None and off.grade is None
    on = {**RULES, "render": {**RULES["render"],
                              "denoise": {"enabled": True, "nr": 12, "nf": -40},
                              "grade": {**RULES["render"]["grade"], "enabled": True}}}  # fmt: skip
    e = build_edl(draft(SEGS), plan(), CLIPS, on, preview=False)
    assert e.denoise == "afftdn=nr=12:nf=-40"
    assert e.grade is not None and e.grade.startswith("eq=saturation=")


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
    assert e.denoise is None and e.grade is None  # finishing filters are off by default
    on = {**RULES, "render": {**RULES["render"], "denoise": {"enabled": True, "nr": 12, "nf": -40}}}
    spoken = build_speech_edl(speech_cuts(), [ov], speech_plan(m), CLIPS, on, preview=True)
    assert spoken.denoise == "afftdn=nr=12:nf=-40"  # the voice is the layer that gets cleaned
    assert [(c.text, c.kind) for c in e.captions] == [("Nhắn tin nhận báo giá sỉ", "cta")]
    assert [[w.text for w in g] for g in e.karaoke] == [["Giò", "lụa", "ngon."],
                                                        ["Nhắn", "tin", "ngay"]]  # fmt: skip


def test_speech_edl_frames_every_other_phrase_closer_and_review_punches_on_taste_words() -> None:
    from gani_dung_video.domain.edl import build_speech_edl

    sales = build_speech_edl(speech_cuts(), [], speech_plan(), CLIPS, RULES, preview=True)
    assert [(s.zoom, s.punch_sec) for s in sales.shots] == [(1.0, None), (1.12, None)]
    p = speech_plan()
    review = p.model_copy(update={"options": {**p.options, "style": "review_mon_an"}})
    e = build_speech_edl(speech_cuts(), [], review, CLIPS, RULES, preview=True)
    # "ngon." at 0.9 s of the first phrase (punctuation ignored); review has no alternate framing
    assert [(s.zoom, s.punch_sec, s.punch_zoom) for s in e.shots] == [
        (1.0, pytest.approx(0.9), pytest.approx(1.08)),
        (1.0, None, 1.0),
    ]


def test_review_speech_mode_slows_fast_broll_and_shares_max_effects_with_punch_ins() -> None:
    from gani_dung_video.domain.edl import BrollPlacement, build_speech_edl
    from gani_dung_video.domain.speech_edit import Overlay

    fast = {**CLIPS, "f.mp4": clip("f.mp4").model_copy(update={"fps": 60.0})}
    p = speech_plan()
    review = p.model_copy(update={"options": {**p.options, "style": "review_mon_an"}})
    ov = BrollPlacement(overlay=Overlay(phrase_id="s_p3", t_start=2.4, t_end=3.6, beats=2),
                        clip="f.mp4", start=5.0, end=10.0, peak=6.0)  # fmt: skip
    e = build_speech_edl(speech_cuts(), [ov], review, fast, RULES, preview=True)
    [o] = e.overlays
    assert o.speed == 0.5 and o.src_out - o.src_in == pytest.approx(0.6)
    assert (o.t_start, o.t_end) == (2.4, 3.6)
    assert e.shots[0].punch_sec is not None  # "ngon." still punches in: 1 + 1 ≤ max_effects 3


def test_speech_edl_refuses_broll_from_an_unknown_clip() -> None:
    from gani_dung_video.domain.edl import BrollPlacement, build_speech_edl
    from gani_dung_video.domain.speech_edit import Overlay

    ov = BrollPlacement(overlay=Overlay(phrase_id="s_p3", t_start=2.4, t_end=3.6, beats=2),
                        clip="zz.mp4", start=0, end=5, peak=1)  # fmt: skip
    with pytest.raises(EdlError, match="zz"):
        build_speech_edl(speech_cuts(), [ov], speech_plan(), CLIPS, RULES, preview=True)


def test_speech_edl_refuses_broll_shorter_than_its_slot() -> None:
    from gani_dung_video.domain.edl import BrollPlacement, build_speech_edl
    from gani_dung_video.domain.speech_edit import Overlay

    ov = BrollPlacement(overlay=Overlay(phrase_id="s_p3", t_start=2.4, t_end=3.6, beats=2),
                        clip="b.mp4", start=5.0, end=5.8, peak=5.4)  # fmt: skip
    with pytest.raises(EdlError, match="B-roll"):
        build_speech_edl(speech_cuts(), [ov], speech_plan(), CLIPS, RULES, preview=True)


# ─────────────── music mode: cut at the chosen length (plan.trim_to_sec) ───────────────


def trimmed(to: float, fade: float = 0.5) -> Plan:
    return plan().model_copy(update={"trim_to_sec": to, "trim_fade_sec": fade})


def test_a_draft_longer_than_the_chosen_length_is_cut_there_with_a_fade() -> None:
    e = build_edl(draft(SEGS), trimmed(13.5), CLIPS, RULES, preview=False)
    assert [(s.t_start, s.t_end) for s in e.shots] == [(0.0, 3.0), (3.0, 6.0), (6.0, 9.0),
                                                       (9.0, 12.0), (12.0, 13.5)]  # fmt: skip
    last = e.shots[-1]
    assert (last.src_in, last.src_out) == (40.0, 41.5) and last.dissolve_out_sec == 0.0
    assert e.content_end == 13.5 and e.duration == 15.5 and e.trim_fade_sec == 0.5
    cta = [c for c in e.captions if c.kind == "cta"]
    assert [(c.start, c.end) for c in cta] == [(12.0, 13.5)]  # the line ends with the picture


def test_a_closing_fragment_shorter_than_the_fade_is_absorbed_by_the_shot_before() -> None:
    e = build_edl(draft(SEGS), trimmed(12.2), CLIPS, RULES, preview=False)
    assert [(s.source, s.t_start, s.t_end) for s in e.shots][-1] == ("c.mp4", 9.0, 12.2)
    assert e.content_end == 12.2 and e.trim_fade_sec == 0.5
    assert [c for c in e.captions if c.kind == "cta"] == []  # the CTA scene was cut away


def test_a_draft_that_already_ends_at_the_chosen_length_is_untouched() -> None:
    e = build_edl(draft(SEGS), trimmed(15.0), CLIPS, RULES, preview=False)
    assert e.content_end == 15.0 and e.trim_fade_sec == 0.0 and len(e.shots) == 5


def test_the_cut_never_leaves_the_last_shot_with_a_dissolve() -> None:
    e = build_edl(draft(SEGS), trimmed(10.0).model_copy(update={}), CLIPS, RULES, preview=False)
    assert e.shots[-1].dissolve_out_sec == 0.0 and e.shots[-1].t_end == 10.0


def test_a_shot_cut_short_keeps_its_in_point_and_loses_its_pad() -> None:
    # c.mp4 is 10 s long: uncut, 6.8 + 3 s + the dissolve runs 0.2 s past it and is padded.
    whole = build_edl(draft(SEGS), plan(), CLIPS, RULES, preview=False).shots[3]
    assert whole.pad_sec > 0
    e = build_edl(draft(SEGS), trimmed(11.0), CLIPS, RULES, preview=False)
    c = e.shots[-1]
    assert c.source == "c.mp4" and (c.src_in, c.src_out) == (6.8, 8.8) and c.pad_sec == 0.0
