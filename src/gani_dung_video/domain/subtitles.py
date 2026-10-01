"""On-screen text as ASS subtitles + an SRT sidecar (phase 5a, spec §7.5). Pure code.

PlayRes is always the final 1080×1920 frame, so the 540p preview scales the same layout.
Text written by the AI or typed by a user is escaped: ASS reads "{...}" as styling commands
and "\\N" as a line break, so a caption must never be able to move, hide or restyle itself.
"""

from __future__ import annotations

from collections.abc import Mapping

from gani_dung_video.domain.ass_text import ass_time, escape_ass
from gani_dung_video.domain.edl import Edl
from gani_dung_video.domain.emphasis import emphasise
from gani_dung_video.domain.karaoke import ass_colour, karaoke_dialogues
from gani_dung_video.domain.preflight import Rules

__all__ = ["ass_document", "ass_time", "escape_ass", "srt_document"]

PLAY_W, PLAY_H = 1080, 1920
_FIELDS = ("Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
           "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
           "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")  # fmt: skip
_POP_STYLES = {"tvc_cat_nhanh"}  # text pops on the beat; other styles fade in/out


def _srt_time(t: float) -> str:
    ms = round(max(t, 0.0) * 1000)
    h, rest = divmod(ms, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _alpha(opacity: float) -> str:
    return f"{round((1.0 - opacity) * 255):02X}"


def _style(name: str, font: str, size: int, colour: str, align: int, ml: int, mr: int, mv: int,
           outline: int = 4, bold: int = 0, secondary: str | None = None) -> str:  # fmt: skip
    sc = secondary or "&H000000FF"
    return (f"Style: {name},{font},{size},{colour},{sc},&H00000000,&H64000000,{bold},0,0,0,"
            f"100,100,0,0,1,{outline},2,{align},{ml},{mr},{mv},1")  # fmt: skip


def _styles(edl: Edl, rules: Rules, sizes: Mapping[str, int] | None) -> list[str]:
    r = rules["render"]
    px, safe, brand = {**r["font_px"], **(sizes or {})}, r["safe"], r["brand"]
    logo_colour = f"&H{_alpha(float(brand['logo_opacity']))}FFFFFF"
    kcfg = rules["speech_edit"]["karaoke"]
    # Speech mode (5c-1): the karaoke line sits at the bottom safe margin, so the CTA moves
    # up by speech_cta_extra_margin to avoid sitting on top of it.
    cta_mv = safe["bottom"] + (int(r["speech_cta_extra_margin"]) if edl.mode == "speech" else 0)
    title = [
        _style("ChapterTitle", edl.font, int(px["chapter_title"]), "&H00FFFFFF", 8, safe["left"],
               safe["right"], safe["top"] + int(r["chapter_title_offset"]), outline=5, bold=1)
    ] if any(c.kind == "title" for c in edl.captions) else []  # fmt: skip
    return [
        *title,
        _style("Caption", edl.font, int(px["caption"]), "&H00FFFFFF", 2, safe["left"],
               safe["right"], safe["bottom"]),
        _style("CTA", edl.font, int(px["cta"]), "&H0000E1FF", 2, safe["left"], safe["right"],
               cta_mv),
        _style("Karaoke", edl.font, int(px["karaoke"]), ass_colour(str(kcfg["base"])), 2,
               safe["left"], safe["right"], safe["bottom"],
               secondary=ass_colour(str(kcfg["highlight"]))),
        _style("Logo", r["fonts"]["default"], int(px["logo"]), logo_colour, 7, 60, 0,
               safe["top"], outline=2),
        _style("EndCardLogo", r["fonts"]["default"], int(px["end_card_logo"]), "&H00FFFFFF", 5,
               safe["left"], safe["right"], 0, outline=0),
        _style("EndCardText", edl.font, int(px["end_card_text"]), "&H00FFFFFF", 5, safe["left"],
               safe["right"], 0, outline=0),
    ]  # fmt: skip


def _dialogue(start: float, end: float, style: str, text: str) -> str:
    return f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}"


def _events(edl: Edl, rules: Rules) -> list[str]:
    r = rules["render"]
    brand = r["brand"]
    fade = "" if edl.style in _POP_STYLES else r"{\fad(200,200)}"
    out = [_dialogue(0.0, edl.content_end, "Logo", escape_ass(brand["logo_text"]))]
    style_of = {"cta": "CTA", "title": "ChapterTitle"}
    pop = r["keyword_highlight"]
    on = rf"{{\c{ass_colour(str(pop['colour']))}\fscx{pop['scale_pct']}\fscy{pop['scale_pct']}}}"
    off = r"{\c&H00FFFFFF&\fscx100\fscy100}"  # Caption and ChapterTitle are white at full size
    for c in edl.captions:
        text = escape_ass(c.text)  # first: the keyword tags added below must stay live
        if edl.emphasis and c.kind != "cta":  # the CTA line is already the accent colour
            text = emphasise(text, edl.emphasis, on, off)
        out.append(_dialogue(c.start, c.end, style_of.get(c.kind, "Caption"), fade + text))
    if edl.mode == "speech" and edl.karaoke:
        out += karaoke_dialogues(edl.karaoke, rules["speech_edit"]["karaoke"], "Karaoke",
                                 edl.emphasis, pop)  # fmt: skip
    t0, t1 = edl.content_end, edl.duration
    cx = PLAY_W // 2
    out += [
        _dialogue(t0, t1, "EndCardLogo", rf"{{\pos({cx},820)}}" + escape_ass(brand["logo_text"])),
        _dialogue(t0, t1, "EndCardText", rf"{{\pos({cx},1020)}}" + escape_ass(brand["zalo"])),
        _dialogue(t0, t1, "EndCardText", rf"{{\pos({cx},1140)\fs44}}"
                  + escape_ass(brand["footer"])),
    ]  # fmt: skip
    return out


def ass_document(edl: Edl, rules: Rules, font_px: Mapping[str, int] | None = None) -> str:
    """Full .ass file: captions, logo watermark, 2 s end card.

    Args:
        font_px: caption/CTA sizes lowered by the QC fit (phase 5b); defaults from rules.
    """
    r = rules["render"]
    brand = r["brand"]
    flags = (("logo: placeholder (logo chữ tạm)", brand["logo_placeholder"]),
             ("zalo: placeholder (số tạm)", brand["zalo_placeholder"]))  # fmt: skip
    notes = [n for n, flag in flags if flag]
    head = ["[Script Info]", "; GANI dựng video — " + "; ".join(notes or ["brand kit thật"]),
            "ScriptType: v4.00+", f"PlayResX: {PLAY_W}", f"PlayResY: {PLAY_H}", "WrapStyle: 0",
            "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", ""]  # fmt: skip
    styles = ["[V4+ Styles]", f"Format: {_FIELDS}", *_styles(edl, rules, font_px), ""]
    events = ["[Events]",
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
              *_events(edl, rules), ""]  # fmt: skip
    return "\n".join(head + styles + events)


def srt_document(edl: Edl) -> str:
    """phude.srt: the captions only (platforms show it as closed captions). Chapter titles are
    headings drawn at the top, not subtitles, and would overlap the first caption: left out."""
    lines = [c for c in edl.captions if c.kind != "title"]
    blocks = [f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{' '.join(c.text.split())}\n"
              for i, c in enumerate(lines, start=1)]  # fmt: skip
    return "\n".join(blocks) + "\n"
