"""Unit tests: the render side of a 3-minute video (phase 7d) — EDL, chapter titles, ASS, QC.

A chapter title is text drawn over the first `title_beats` beats of the chapter (decision Q1 of
01/10/2026): it adds no time, so the beat grid does not move. Dissolves happen between chapters,
not between the ~5 sub-blocks inside one. The drafts come from a fake Claude over mock footage.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from chapter_drafts import OPTS, build_answer, make_plan_and_pick, rules_on

from gani_dung_video.application.draft_service import DraftsFile, build_drafts
from gani_dung_video.domain.drafts import SnappedDraft
from gani_dung_video.domain.edl import Caption, Edl, build_edl
from gani_dung_video.domain.footage import Clip
from gani_dung_video.domain.qc import check_captions, fit_font_px
from gani_dung_video.domain.subtitles import ass_document, srt_document
from gani_dung_video.infrastructure.claude_client import AiReply
from gani_dung_video.infrastructure.config_files import load_glossary, load_rules

SHIPPED = load_rules()
GLOSSARY = load_glossary()
RULES = rules_on()
PLAN, PICK = make_plan_and_pick(RULES)


def clip(name: str) -> Clip:
    return Clip(file=name, size_bytes=1, duration_sec=60.0, width=1080, height=1920, rotation=0,
                orientation="doc", fps=30.0, video_codec="h264", pix_fmt="yuv420p",
                color_transfer=None, color_primaries=None, is_hdr=False, has_audio_stream=True,
                audio_tracks=1)  # fmt: skip


CLIPS: Mapping[str, Clip] = {f"clip{i:02d}.mp4": clip(f"clip{i:02d}.mp4") for i in range(28)}


def drafted(tmp_path: Path) -> list[SnappedDraft]:
    edit = tmp_path / "edit"
    edit.mkdir()
    (edit / "plan.json").write_text(PLAN.model_dump_json(), encoding="utf-8")
    (edit / "candidates.json").write_text(PICK.model_dump_json(), encoding="utf-8")
    opts = {"phong_cach": OPTS["phong_cach"], "muc_dich": OPTS["muc_dich"], "do_dai_giay": 180,
            "so_ban_nhap": 2, "nhac": "khong_nhac"}  # fmt: skip
    (tmp_path / "o.json").write_text(json.dumps(opts), encoding="utf-8")

    def fake(system: str, user: str, schema: Mapping[str, Any]) -> AiReply:
        text = json.dumps(build_answer(PLAN, PICK), ensure_ascii=False)
        return AiReply(text=text, input_tokens=14000, output_tokens=8000,
                       stop_reason="end_turn", model="fake")  # fmt: skip

    result, _ = build_drafts(tmp_path / "o.json", tmp_path, RULES, GLOSSARY, caller=fake)
    assert isinstance(result, DraftsFile)
    return result.drafts


@pytest.fixture(scope="module")
def draft_a(tmp_path_factory: pytest.TempPathFactory) -> SnappedDraft:
    return drafted(tmp_path_factory.mktemp("d180"))[0]


@pytest.fixture(scope="module")
def edl(draft_a: SnappedDraft) -> Edl:
    return build_edl(draft_a, PLAN, CLIPS, RULES, preview=True)


# ─────────────── dissolves: between chapters only ───────────────


def group_of(block: str) -> str:
    chapter = next((b.chapter for b in PLAN.blocks if b.role == block), None)
    return chapter or block


def test_dissolves_happen_only_where_the_chapter_changes(edl: Edl) -> None:
    groups = [group_of(s.block) for s in edl.shots]
    for i, shot in enumerate(edl.shots[:-1]):
        changes = groups[i] != groups[i + 1]
        assert (shot.dissolve_out_sec > 0) == changes, (i, shot.block, edl.shots[i + 1].block)
    assert sum(1 for s in edl.shots if s.dissolve_out_sec > 0) == len(PLAN.chapters) + 1


# ─────────────── chapter titles ───────────────


def titles(e: Edl) -> list[Caption]:
    return [c for c in e.captions if c.kind == "title"]


def test_each_chapter_gets_its_title_over_its_first_beats(edl: Edl, draft_a: SnappedDraft) -> None:
    got = titles(edl)
    assert [c.text for c in got] == [t.title for t in draft_a.chapter_titles]
    for c, chapter in zip(got, PLAN.chapters, strict=True):
        mine = [s for s in draft_a.segments if s.block in chapter.blocks]
        assert c.start == pytest.approx(mine[0].t_start, abs=1 / 30)
        want = chapter.title_beats * PLAN.beat_sec
        assert c.end - c.start == pytest.approx(want, abs=1 / 30)
        assert c.end <= mine[-1].t_end + 1e-6


def test_a_title_never_outlasts_a_short_chapter(draft_a: SnappedDraft) -> None:
    tiny = PLAN.model_copy(update={"chapters": [
        c.model_copy(update={"title_beats": 500}) for c in PLAN.chapters]})  # fmt: skip
    e = build_edl(draft_a, tiny, CLIPS, RULES, preview=True)
    for c, chapter in zip(titles(e), PLAN.chapters, strict=True):
        last = [s for s in draft_a.segments if s.block in chapter.blocks][-1]
        assert c.end <= last.t_end + 1e-6


def test_text_captions_are_the_segment_lines_only(edl: Edl, draft_a: SnappedDraft) -> None:
    lines = [s for s in draft_a.segments if s.text.strip()]
    assert len([c for c in edl.captions if c.kind != "title"]) == len(lines)
    assert [c.kind for c in edl.captions if c.kind == "cta"] == ["cta"]


def test_captions_are_in_time_order_and_every_time_is_on_a_frame(edl: Edl) -> None:
    starts = [c.start for c in edl.captions]
    assert starts == sorted(starts)
    for c in edl.captions:
        assert round(c.start * 30) / 30 == pytest.approx(c.start)
        assert round(c.end * 30) / 30 == pytest.approx(c.end)


def test_a_draft_without_titles_has_no_title_captions(draft_a: SnappedDraft) -> None:
    bare = draft_a.model_copy(update={"chapter_titles": []})
    assert titles(build_edl(bare, PLAN, CLIPS, RULES, preview=True)) == []


def test_the_end_to_end_timeline_does_not_move(edl: Edl) -> None:
    assert edl.content_end == pytest.approx(PLAN.planned_beats * PLAN.beat_sec, abs=0.05)
    assert edl.duration == pytest.approx(edl.content_end + RULES["render"]["end_card_sec"])


# ─────────────── ASS and SRT ───────────────


def test_ass_has_a_top_centre_style_and_a_dialogue_per_title(edl: Edl) -> None:
    doc = ass_document(edl, RULES)
    style = next(ln for ln in doc.splitlines() if ln.startswith("Style: ChapterTitle"))
    fields = style.split(",")
    assert int(fields[2]) == RULES["render"]["font_px"]["chapter_title"]
    assert fields[18] == "8"  # Alignment 8 = top centre (Logo is 7 = top left)
    dialogues = [ln for ln in doc.splitlines() if ",ChapterTitle," in ln]
    assert len(dialogues) == len(PLAN.chapters)
    assert "Khởi đầu sạch sẽ" in dialogues[0]


def test_the_title_sits_below_the_logo_row() -> None:
    r = RULES["render"]
    assert r["safe"]["top"] > 0 and r["font_px"]["chapter_title"] > r["font_px"]["caption"]


def test_a_title_cannot_restyle_itself(edl: Edl) -> None:
    sneaky = edl.model_copy(update={"captions": [Caption(start=0.0, end=1.4, kind="title",
                                                         text=r"{\an5\fs200}Lừa")]})  # fmt: skip
    line = next(ln for ln in ass_document(sneaky, RULES).splitlines() if ",ChapterTitle," in ln)
    assert r"{\an5" not in line


def test_a_short_video_ass_is_unchanged_by_the_title_style() -> None:
    plain = Edl(style="ke_chuyen_nghe", width=540, height=960, fps=30, video_kbps=2500, shots=[],
                content_end=3.0, end_card_sec=2.0, duration=5.0, music=None, ambient_db=None,
                lufs=-14.0, true_peak_db=-2.0, font="Be Vietnam Pro ExtraBold",
                captions=[Caption(start=0.0, end=1.0, text="Xin chào", kind="text")])  # fmt: skip
    doc = ass_document(plain, RULES)
    assert "ChapterTitle" not in doc


def test_srt_keeps_the_captions_and_leaves_the_titles_out(edl: Edl) -> None:
    text = srt_document(edl)
    assert "Khởi đầu sạch sẽ" not in text
    assert text.count("-->") == len([c for c in edl.captions if c.kind != "title"])


# ─────────────── QC: titles live in their own lane ───────────────

PX = {"caption": 72, "cta": 80, "chapter_title": 88}


def width(text: str, font: str, size: int) -> float:
    return len(text) * size * 0.5


def test_a_title_over_the_first_caption_is_not_a_t6_overlap(edl: Edl) -> None:
    both = [Caption(start=0.0, end=1.4, text="Khởi đầu sạch sẽ", kind="title"),
            Caption(start=0.0, end=2.0, text="Một ngày ở xưởng", kind="text")]  # fmt: skip
    found = check_captions(edl.model_copy(update={"captions": both}), RULES, PX, width)
    assert [f.code for f in found] == []


def test_two_bottom_captions_still_cannot_overlap() -> None:
    caps = [Caption(start=0.0, end=2.0, text="Một", kind="text"),
            Caption(start=1.0, end=3.0, text="Hai", kind="cta")]  # fmt: skip
    e = Edl(style="ke_chuyen_nghe", width=540, height=960, fps=30, video_kbps=2500, shots=[],
            content_end=3.0, end_card_sec=2.0, duration=5.0, music=None, ambient_db=None,
            lufs=-14.0, true_peak_db=-2.0, font="f", captions=caps)  # fmt: skip
    assert [f.code for f in check_captions(e, RULES, PX, width)] == ["T6"]


def test_a_long_title_is_sized_with_the_title_size_and_can_be_shrunk() -> None:
    long = "Một tiêu đề khá dài cho chương này"
    e = Edl(style="ke_chuyen_nghe", width=540, height=960, fps=30, video_kbps=2500, shots=[],
            content_end=3.0, end_card_sec=2.0, duration=5.0, music=None, ambient_db=None,
            lufs=-14.0, true_peak_db=-2.0, font="f",
            captions=[Caption(start=0.0, end=1.4, text=long, kind="title")])  # fmt: skip
    narrow = {**PX, "chapter_title": 88}
    found = check_captions(e, RULES, narrow, lambda t, f, s: len(t) * s * 0.9)
    assert [f.code for f in found] == ["T7"]  # too many lines at 88 px
    fitted = fit_font_px(e, RULES, narrow, lambda t, f, s: len(t) * s * 0.9)
    assert fitted is None or fitted["chapter_title"] < 88
    assert fitted is None or fitted["caption"] == 72  # only the title size moved


# ─────────────── audit 5: two captions that start together are fitted separately ───────────────

AT_THE_SAME_MOMENT = "Giò chả tươi mười ngày"  # too long at 88 px, fine a few steps lower


def _same_start(first: Caption, second: Caption) -> Edl:
    return Edl(style="ke_chuyen_nghe", width=540, height=960, fps=30, video_kbps=2500, shots=[],
               content_end=3.0, end_card_sec=2.0, duration=5.0, music=None, ambient_db=None,
               lufs=-14.0, true_peak_db=-2.0, font="f", captions=[first, second])  # fmt: skip


def _measure(t: str, f: str, s: int) -> float:
    return len(t) * s * 0.9


def test_a_title_and_a_caption_that_start_together_shrink_only_the_title() -> None:
    """A chapter title sits on the chapter's first cut, where the first caption starts too. The
    fit keyed sizes by start time, so the caption's kind hid the title's and the video was
    refused although the title alone fits a few pixels down."""
    title = Caption(start=5.0, end=8.0, text=AT_THE_SAME_MOMENT, kind="title")
    text = Caption(start=5.0, end=7.0, text="Giò lụa", kind="text")
    narrow = {**PX, "chapter_title": 88}
    alone = fit_font_px(_same_start(title, text.model_copy(update={"start": 6.0})), RULES,
                        narrow, _measure)  # fmt: skip
    assert alone is not None and alone["chapter_title"] < 88  # the title can be fitted
    together = fit_font_px(_same_start(title, text), RULES, narrow, _measure)
    assert together == alone  # the same answer when they start at the same moment
    assert together is not None and together["caption"] == 72  # the caption size never moved


def test_the_answer_does_not_depend_on_which_caption_is_listed_first() -> None:
    title = Caption(start=5.0, end=8.0, text=AT_THE_SAME_MOMENT, kind="title")
    text = Caption(start=5.0, end=7.0, text="Giò lụa", kind="text")
    narrow = {**PX, "chapter_title": 88}
    one = fit_font_px(_same_start(title, text), RULES, narrow, _measure)
    two = fit_font_px(_same_start(text, title), RULES, narrow, _measure)
    assert one == two and one is not None


def test_a_cta_and_a_caption_that_start_together_shrink_only_the_cta() -> None:
    cta = Caption(start=9.0, end=12.0, text="Nhắn tin nhận báo giá sỉ", kind="cta")
    text = Caption(start=9.0, end=12.0, text="Giò lụa", kind="text")
    sizes = {**PX, "cta": 96}
    fitted = fit_font_px(_same_start(cta, text), RULES, sizes, _measure)
    assert fitted is not None and fitted["cta"] < 96 and fitted["caption"] == 72


def test_text_that_cannot_fit_even_at_the_floor_still_gives_up() -> None:
    giant = "từ " * 60
    title = Caption(start=5.0, end=8.0, text=giant, kind="title")
    text = Caption(start=5.0, end=7.0, text="Giò lụa", kind="text")
    assert fit_font_px(_same_start(title, text), RULES, {**PX, "chapter_title": 88},
                       _measure) is None  # fmt: skip
