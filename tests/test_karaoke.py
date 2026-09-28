"""Unit tests: domain/karaoke.py — word-by-word highlighted subtitles (5c-1)."""

from __future__ import annotations

import re

from gani_dung_video.domain.karaoke import ass_colour, group_words, karaoke_dialogues
from gani_dung_video.domain.speech import SpokenWord
from gani_dung_video.infrastructure.config_files import load_rules

KCFG = load_rules()["speech_edit"]["karaoke"]


def words(spec: str, start: float = 0.0, step: float = 0.3, gaps: dict[int, float] | None = None
          ) -> list[SpokenWord]:  # fmt: skip
    out, t = [], start
    for i, w in enumerate(spec.split()):
        t += (gaps or {}).get(i, 0.0)
        out.append(SpokenWord(text=w, start=round(t, 3), end=round(t + step, 3), prob=0.9))
        t += step
    return out


def sizes(groups: list[list[SpokenWord]]) -> list[int]:
    return [len(g) for g in groups]


def test_colour_is_converted_to_ass_bgr() -> None:
    assert ass_colour("#FFCC00") == "&H0000CCFF&" and ass_colour("#FFFFFF") == "&H00FFFFFF&"


def test_groups_hold_3_to_5_words_balanced() -> None:
    assert sizes(group_words(words("a b c d e f g"), KCFG)) == [4, 3]  # not 5 + 2
    assert sizes(group_words(words("a b c d e f"), KCFG)) == [3, 3]
    assert sizes(group_words(words("a b c d e"), KCFG)) == [5]
    assert sizes(group_words(words("a b"), KCFG)) == [2]  # too few to reach 3: one short group


def test_groups_break_at_sentence_ends_and_pauses() -> None:
    got = group_words(words("Giò lụa ngon. Đặt ngay hôm nay", gaps={3: 0.0}), KCFG)
    assert sizes(got) == [3, 4]
    paused = group_words(words("a b c d e f", gaps={3: 0.5}), KCFG)
    assert sizes(paused) == [3, 3] and paused[1][0].text == "d"


def test_one_dialogue_per_word_with_only_that_word_highlighted() -> None:
    ws = words("Giò lụa GANI")
    lines = karaoke_dialogues([ws], KCFG, style="Karaoke")
    assert len(lines) == 3
    yellow, white = ass_colour("#FFCC00"), ass_colour("#FFFFFF")
    for i, ln in enumerate(lines):
        assert ln.startswith("Dialogue: 0,") and ",Karaoke," in ln
        highlighted = re.findall(r"\{\\c" + re.escape(yellow) + r"\}([^{]+)\{", ln)
        assert highlighted == [ws[i].text]
        assert white in ln


def test_the_group_stays_on_screen_without_flicker_between_words() -> None:
    ws = words("một hai ba", gaps={1: 0.1})
    lines = karaoke_dialogues([ws], KCFG, style="Karaoke")
    times = [re.match(r"Dialogue: 0,([\d:.]+),([\d:.]+),", ln).groups() for ln in lines]  # type: ignore[union-attr]
    # "hai" starts at 0.4 s (after a 0.1 s pause); "một" stays lit until then: no blank gap
    assert [t[0] for t in times] == ["0:00:00.00", "0:00:00.40", "0:00:00.70"]
    assert all(times[i][1] == times[i + 1][0] for i in range(len(times) - 1))  # no gap


def test_spoken_text_cannot_inject_ass_tags() -> None:
    ws = words(r"giảm {\pos(0,0)}50%")
    lines = karaoke_dialogues([ws], KCFG, style="Karaoke")
    assert all(r"\pos" not in ln for ln in lines)
