"""On-screen text as ASS subtitles + an SRT sidecar (phase 5a, spec §7.5). Pure code.

PlayRes is always the final 1080×1920 frame, so the 540p preview scales the same layout.
Text written by the AI or typed by a user is escaped: ASS reads "{...}" as styling commands
and "\\N" as a line break, so a caption must never be able to move, hide or restyle itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gani_dung_video.domain.edl import Edl
from gani_dung_video.domain.preflight import Rules

PLAY_W, PLAY_H = 1080, 1920
_FIELDS = ("Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
           "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
           "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding")  # fmt: skip
_POP_STYLES = {"tvc_cat_nhanh"}  # text pops on the beat; other styles fade in/out


def ass_time(t: float) -> str:
    """Seconds → ASS time h:mm:ss.cc."""
    cs = round(max(t, 0.0) * 100)
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s, c = divmod(rest, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _srt_time(t: float) -> str:
    ms = round(max(t, 0.0) * 1000)
    h, rest = divmod(ms, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def escape_ass(text: str) -> str:
    """Make user/AI text inert: no override blocks, no escape sequences, one line."""
    return " ".join(text.split()).replace("\\", "/").replace("{", "(").replace("}", ")")


def _alpha(opacity: float) -> str:
    return f"{round((1.0 - opacity) * 255):02X}"


def _style(name: str, font: str, size: int, colour: str, align: int, ml: int, mr: int, mv: int,
           outline: int = 4, bold: int = 0) -> str:  # fmt: skip
    return (f"Style: {name},{font},{size},{colour},&H000000FF,&H00000000,&H64000000,{bold},0,0,0,"
            f"100,100,0,0,1,{outline},2,{align},{ml},{mr},{mv},1")  # fmt: skip


def _styles(edl: Edl, r: Mapping[str, Any]) -> list[str]:
    px, safe, brand = r["font_px"], r["safe"], r["brand"]
    logo_colour = f"&H{_alpha(float(brand['logo_opacity']))}FFFFFF"
    return [
        _style("Caption", edl.font, int(px["caption"]), "&H00FFFFFF", 2, safe["left"],
               safe["right"], safe["bottom"]),
        _style("CTA", edl.font, int(px["cta"]), "&H0000E1FF", 2, safe["left"], safe["right"],
               safe["bottom"]),
        _style("Logo", r["fonts"]["default"], int(px["logo"]), logo_colour, 7, 60, 0,
               safe["top"], outline=2),
        _style("EndCardLogo", r["fonts"]["default"], int(px["end_card_logo"]), "&H00FFFFFF", 5,
               safe["left"], safe["right"], 0, outline=0),
        _style("EndCardText", edl.font, int(px["end_card_text"]), "&H00FFFFFF", 5, safe["left"],
               safe["right"], 0, outline=0),
    ]  # fmt: skip


def _dialogue(start: float, end: float, style: str, text: str) -> str:
    return f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}"


def _events(edl: Edl, r: Mapping[str, Any]) -> list[str]:
    brand = r["brand"]
    fade = "" if edl.style in _POP_STYLES else r"{\fad(200,200)}"
    out = [_dialogue(0.0, edl.content_end, "Logo", escape_ass(brand["logo_text"]))]
    for c in edl.captions:
        out.append(_dialogue(c.start, c.end, "CTA" if c.kind == "cta" else "Caption",
                             fade + escape_ass(c.text)))  # fmt: skip
    t0, t1 = edl.content_end, edl.duration
    cx = PLAY_W // 2
    out += [
        _dialogue(t0, t1, "EndCardLogo", rf"{{\pos({cx},820)}}" + escape_ass(brand["logo_text"])),
        _dialogue(t0, t1, "EndCardText", rf"{{\pos({cx},1020)}}" + escape_ass(brand["zalo"])),
        _dialogue(t0, t1, "EndCardText", rf"{{\pos({cx},1140)\fs44}}"
                  + escape_ass(brand["footer"])),
    ]  # fmt: skip
    return out


def ass_document(edl: Edl, rules: Rules) -> str:
    """Full .ass file: captions, logo watermark, 2 s end card."""
    r = rules["render"]
    brand = r["brand"]
    flags = (("logo: placeholder (logo chữ tạm)", brand["logo_placeholder"]),
             ("zalo: placeholder (số tạm)", brand["zalo_placeholder"]))  # fmt: skip
    notes = [n for n, flag in flags if flag]
    head = ["[Script Info]", "; GANI dựng video — " + "; ".join(notes or ["brand kit thật"]),
            "ScriptType: v4.00+", f"PlayResX: {PLAY_W}", f"PlayResY: {PLAY_H}", "WrapStyle: 0",
            "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", ""]  # fmt: skip
    styles = ["[V4+ Styles]", f"Format: {_FIELDS}", *_styles(edl, r), ""]
    events = ["[Events]",
              "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
              *_events(edl, r), ""]  # fmt: skip
    return "\n".join(head + styles + events)


def srt_document(edl: Edl) -> str:
    """phude.srt: the captions only (platforms show it as closed captions)."""
    blocks = [f"{i}\n{_srt_time(c.start)} --> {_srt_time(c.end)}\n{' '.join(c.text.split())}\n"
              for i, c in enumerate(edl.captions, start=1)]  # fmt: skip
    return "\n".join(blocks) + "\n"
