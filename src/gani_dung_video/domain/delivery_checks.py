"""E1 — objective checks on a finished video (Dev Package 03, T-E, decision 06/10/2026). Pure code.

T1–T7 (domain/qc.py) already judge the container, the frame count, loudness, black frames,
freezes and caption overlap. E1 adds what the owner asked for on top: the length is the length
that was chosen (E1-DUR), every text sits inside the platform safe zone (E1-SAFE), every
character has a glyph in the font (E1-GLYPH) and no banned claim is on screen (C1). Each check
returns an `E1Item` — pass/fail together with the number it was judged on — and the `Finding`s
that stop delivery (level "error"). Only what a number can decide is automated: whether the
video is good or beautiful is still the reviewer's call.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from gani_dung_video.domain.compliance import check_edl_claims, edl_texts
from gani_dung_video.domain.edl import Edl
from gani_dung_video.domain.glossary import find_phrases
from gani_dung_video.domain.preflight import Rules
from gani_dung_video.domain.qc import PLAY_W, Finding, WidthFn, size_key, usable_width, wrap_lines
from gani_dung_video.domain.subtitles import PLAY_H

GlyphFn = Callable[[str, str], bool]  # (character, font family) → the font draws it
_EPS = 1e-6


class E1Item(BaseModel):
    """One check: pass/fail and the number behind it."""

    model_config = ConfigDict(frozen=True)

    code: str
    name: str  # Vietnamese, shown to the editor
    passed: bool
    value: float | None = None  # what was measured (None when there was nothing to measure)
    limit: float | None = None  # the limit it was compared with
    unit: str = ""
    detail: str  # the numbers in words, Vietnamese


class E1Report(BaseModel):
    passed: bool
    items: list[E1Item]
    # Seconds the content length is off the length the user chose / the plan promised
    # (negative = shorter). Always written, whatever rules.qc.duration_reference says.
    deviation_vs_chosen: float | None = None
    deviation_vs_plan: float | None = None


def _outcome(item: E1Item, code: str | None = None) -> tuple[E1Item, list[Finding]]:
    """The item plus the one blocking Finding it raises when it failed."""
    stop = [] if item.passed else [Finding(code=code or item.code, level="error", msg=item.detail)]
    return item, stop


# ─────────────── E1-DUR ───────────────


def duration_deviations(
    file_sec: float, e: Edl, chosen_sec: int, planned_sec: float
) -> tuple[float, float]:
    """(content minus chosen, content minus planned) in seconds; content = file minus end card."""
    content = file_sec - e.end_card_sec
    return round(content - chosen_sec, 3), round(content - planned_sec, 3)


def _detail(file_sec: float, e: Edl, chosen_sec: int, planned_sec: float) -> str:
    vs_chosen, vs_plan = duration_deviations(file_sec, e, chosen_sec, planned_sec)
    return (f"Độ dài nội dung {file_sec - e.end_card_sec:.2f}s (file {file_sec:.2f}s trừ khung kết "
            f"{e.end_card_sec:g}s); kế hoạch {planned_sec:.2f}s, đã chọn {chosen_sec}s. Lệch so "
            f"với đã chọn {vs_chosen:+.2f}s, so với kế hoạch {vs_plan:+.2f}s.")  # fmt: skip


def check_duration(
    file_sec: float, e: Edl, chosen_sec: int, planned_sec: float, rules: Rules
) -> tuple[E1Item, list[Finding]]:
    """The content length is within the allowed gap of its reference. `value` is the signed gap
    to the reference in seconds, `limit` the allowed gap.

    Beat-driven videos (music, grid) are held to rules.qc.duration_tolerance_sec around the
    plan's own length ("plan", the default for now) or the chosen length ("chosen"), see
    rules.qc.duration_reference. The plan length is the chosen length rounded to whole scenes
    on the beat grid (spec §7.2); E1-DUR-GAP keeps it near the chosen length.

    Speech videos follow the voice, so they get the wider rules.qc.duration_tolerance_voice_sec
    around the chosen length: inside it a gap that exceeds the normal tolerance is a warning;
    outside it delivery stops."""
    cfg = rules["qc"]
    tol = float(cfg["duration_tolerance_sec"][str(chosen_sec)])
    vs_chosen, vs_plan = duration_deviations(file_sec, e, chosen_sec, planned_sec)
    detail = _detail(file_sec, e, chosen_sec, planned_sec)
    voice = e.mode == "speech"
    if voice:
        v = cfg["duration_tolerance_voice_sec"][str(chosen_sec)]
        limit, gap = max(float(v["pct"]) * chosen_sec, float(v["sec"])), vs_chosen
        detail += (
            f" Chế độ lời: cho phép ±{limit:g}s ({float(v['pct']) * 100:g}% hoặc {v['sec']}s)."
        )
    else:
        by_plan = cfg["duration_reference"] == "plan"
        limit, gap = tol, vs_plan if by_plan else vs_chosen
        detail += f" Cho phép ±{limit:g}s so với {'kế hoạch' if by_plan else 'độ dài đã chọn'}."
    ok = abs(gap) <= limit + _EPS
    item = E1Item(code="E1-DUR", name="Độ dài đúng với độ dài đã chọn", passed=ok, value=gap,
                  limit=limit, unit="s", detail=detail)  # fmt: skip
    if not ok:
        return item, [Finding(code="E1-DUR", level="error", msg=detail)]
    if voice and abs(gap) > tol + _EPS:  # inside the voice limit but not the normal one
        return item, [Finding(code="E1-DUR", level="warn", msg=detail)]
    return item, []


def check_duration_gap(
    chosen_sec: int, planned_sec: float, rules: Rules
) -> tuple[E1Item, list[Finding]]:
    """E1-DUR-GAP: the plan's length stays within rules.qc.duration_plan_gap_pct of the chosen
    length, so reading E1-DUR against the plan cannot hide a plan that drifted away from what
    the user asked for. `value` is planned minus chosen, in seconds."""
    pct = float(rules["qc"]["duration_plan_gap_pct"])
    limit, gap = pct * chosen_sec, planned_sec - chosen_sec
    detail = (f"Độ dài kế hoạch {planned_sec:.2f}s, đã chọn {chosen_sec}s: lệch {gap:+.2f}s, cho "
              f"phép ±{pct * 100:g}% = ±{limit:g}s.")  # fmt: skip
    if gap > 0 and abs(gap) > limit + _EPS:
        detail += (" Nhịp bài nhạc làm mỗi khối dài hơn: chọn bài có BPM hợp phong cách hoặc độ "
                   "dài video dài hơn.")  # fmt: skip
    item = E1Item(code="E1-DUR-GAP", name="Kế hoạch không lệch xa độ dài đã chọn",
                  passed=abs(gap) <= limit + _EPS, value=round(gap, 3), limit=round(limit, 3),
                  unit="s", detail=detail)  # fmt: skip
    return _outcome(item)


# ─────────────── E1-SAFE ───────────────


@dataclass(frozen=True)
class ScreenText:
    text: str
    kind: str  # text | cta | title | karaoke
    start: float


def screen_texts(e: Edl) -> list[ScreenText]:
    """Every text drawn by the caption styles: captions, chapter titles, CTA, karaoke lines."""
    out = [ScreenText(c.text, c.kind, c.start) for c in e.captions]
    out += [ScreenText(" ".join(w.text for w in g), "karaoke", g[0].start) for g in e.karaoke if g]
    return out


def _layout(kind: str, e: Edl, rules: Rules) -> tuple[str, int, bool]:
    """(font size key, MarginV, drawn from the top) — the same layout as subtitles._styles
    (a test reads the real .ass to keep the two from drifting apart)."""
    r = rules["render"]
    safe = r["safe"]
    if kind == "title":
        return "chapter_title", int(safe["top"]) + int(r["chapter_title_offset"]), True
    if kind == "karaoke":
        return "karaoke", int(safe["bottom"]), False
    extra = int(r["speech_cta_extra_margin"]) if kind == "cta" and e.mode == "speech" else 0
    return size_key(kind), int(safe["bottom"]) + extra, False


def _clearances(
    t: ScreenText, e: Edl, rules: Rules, px: Mapping[str, int], width: WidthFn
) -> dict[str, float]:
    """Pixels between the text block and each edge of the frame (a line is as tall as its
    font size, libass' ascent minus descent; the outline is not counted)."""
    key, margin_v, from_top = _layout(t.kind, e, rules)
    size = int(px.get(key, rules["render"]["font_px"][key]))
    lines = wrap_lines(t.text, usable_width(rules), lambda s: width(s, e.font, size))
    block_w = max((width(line, e.font, size) for line in lines), default=0.0)
    height = len(lines) * size
    top = float(margin_v) if from_top else float(PLAY_H - margin_v - height)
    side = (PLAY_W - block_w) / 2
    return {"trên": top, "dưới": PLAY_H - (top + height), "trái": side, "phải": side}


_ZONE_KEY = {"trên": "top", "dưới": "bottom", "trái": "left", "phải": "right"}


def check_safe_zone(
    e: Edl, rules: Rules, px: Mapping[str, int], width: WidthFn
) -> tuple[E1Item, list[Finding]]:
    """Every on-screen text keeps its distance from the four edges (rules.qc.safe_zone).
    `value` is the smallest spare distance in pixels over all texts and edges (< 0 = outside)."""
    zone = {k: int(rules["qc"]["safe_zone"][v]) for k, v in _ZONE_KEY.items()}
    stops: list[Finding] = []
    least = {k: float("inf") for k in zone}  # smallest clearance per edge
    for t in screen_texts(e):
        clear = _clearances(t, e, rules, px, width)
        least = {k: min(least[k], clear[k]) for k in zone}
        for edge, d in clear.items():
            if d < zone[edge] - _EPS:
                stops.append(Finding(code="E1-SAFE", level="error", at_sec=t.start,
                                     msg=f"Chữ '{t.text}' cách mép {edge} {d:.0f}px, vùng an toàn "
                                         f"cần ≥ {zone[edge]}px."))  # fmt: skip
    if least["trên"] == float("inf"):
        return _outcome(E1Item(code="E1-SAFE", name="Chữ trong vùng an toàn", passed=True,
                               detail="Không có chữ trên màn hình."))  # fmt: skip
    spare = min(least[k] - zone[k] for k in zone)
    detail = ("Chừa lề nhỏ nhất (vùng an toàn): " + ", ".join(
        f"{k} {least[k]:.0f}px ({zone[k]}px)" for k in zone) + ".")  # fmt: skip
    item = E1Item(code="E1-SAFE", name="Chữ trong vùng an toàn", passed=not stops,
                  value=round(spare, 1), limit=0.0, unit="px", detail=detail)  # fmt: skip
    return item, stops


# ─────────────── E1-GLYPH ───────────────


def check_glyphs(
    e: Edl, brand: Sequence[tuple[str, str]], has_glyph: GlyphFn
) -> tuple[E1Item, list[Finding]]:
    """Every character of every text has a glyph in the font it is drawn with, so Vietnamese
    marks are never replaced by empty boxes. `brand` = (text, font family) of the logo and the
    end card, which are drawn too. `value` is the number of characters without a glyph."""
    texts = [(t.text, e.font) for t in screen_texts(e)] + list(brand)
    seen: set[tuple[str, str]] = set()
    for text, font in texts:
        for ch in unicodedata.normalize("NFC", text):
            if not ch.isspace() and unicodedata.category(ch)[0] != "C":
                seen.add((ch, font))
    missing = sorted({ch for ch, font in seen if not has_glyph(ch, font)})
    marked = len({ch for ch, _ in seen if unicodedata.normalize("NFD", ch) != ch})
    shown = ", ".join(f"'{c}' (U+{ord(c):04X})" for c in missing)
    detail = (
        f"Đã soát {len({c for c, _ in seen})} ký tự khác nhau, trong đó {marked} chữ có dấu "
        "tiếng Việt: " + (f"thiếu glyph cho {shown}." if missing else "đủ glyph.")
    )
    item = E1Item(code="E1-GLYPH", name="Đủ glyph, dấu tiếng Việt không mất", passed=not missing,
                  value=float(len(missing)), limit=0.0, unit="ký tự", detail=detail)  # fmt: skip
    return _outcome(item)


# ─────────────── C1 on screen ───────────────


def check_onscreen_claims(e: Edl, banned: Sequence[str]) -> tuple[E1Item, list[Finding]]:
    """C1 again on the finished video's texts (the gate before ffmpeg already refuses to
    render them; this is the proof written into the manifest). `value` = number of hits."""
    texts = edl_texts(e)
    hits = check_edl_claims(e, banned)
    detail = f"Đã soát {len(texts)} đoạn chữ: " + (
        "; ".join(f"{h.source}: '{h.text}' chứa cụm cấm '{h.phrase}'" for h in hits)
        if hits
        else "không có cụm cấm."
    )
    item = E1Item(code="C1", name="Chữ không chứa cụm cấm (C1)", passed=not hits,
                  value=float(len(hits)), limit=0.0, unit="cụm", detail=detail)  # fmt: skip
    return item, [Finding(code="C1", level="error", msg=detail)] if hits else []


# ─────────────── platform risk words (14/10/2026) ───────────────


def check_platform_words(e: Edl, words: Sequence[str]) -> tuple[E1Item, list[Finding]]:
    """Words the target platform tends to limit, on screen or spoken (karaoke lines are the
    voice). A warning per hit, never a block: the editor decides. `value` = number of hits."""
    if not e.platform:
        return E1Item(code="E1-NEN-TANG", name="Từ ngữ rủi ro theo nền tảng", passed=True,
                      detail="Video không dựng theo nền tảng: không soát."), []  # fmt: skip
    found: list[Finding] = []
    used: set[str] = set()
    for t in screen_texts(e):
        for w in find_phrases(t.text, words):
            used.add(w)
            where = "lời nói" if t.kind == "karaoke" else "chữ trên màn hình"
            found.append(Finding(code="E1-NEN-TANG", level="warn", at_sec=t.start,
                                 msg=f"Bản {e.platform}: {where} '{t.text}' có từ '{w}' mà nền "
                                     "tảng hay hạn chế (dễ bị giảm phân phối). Cân nhắc đổi "
                                     "câu ở bản nháp."))  # fmt: skip
    listed = ", ".join(f"'{w}'" for w in sorted(used))
    detail = (f"Bản {e.platform}: {len(found)} chỗ dùng {listed} (chỉ cảnh báo, không chặn)."
              if found else f"Bản {e.platform}: không có từ nào trong danh sách.")  # fmt: skip
    item = E1Item(code="E1-NEN-TANG", name="Từ ngữ rủi ro theo nền tảng", passed=True,
                  value=float(len(found)), unit="chỗ", detail=detail)  # fmt: skip
    return item, found


# ─────────────── report ───────────────


def _blocked(findings: Sequence[Finding], codes: Sequence[str]) -> list[Finding]:
    return [f for f in findings if f.code in codes and f.level in ("error", "fix")]


def _format_item(e: Edl, rules: Rules, found: Sequence[Finding]) -> E1Item:
    """T1/T2 (container, frame count) plus the 9:16 frame the naming ratio promises."""
    ratio_ok = e.width * 16 == e.height * 9
    bad = _blocked(found, ("T1", "T2"))
    detail = (
        f"{e.width}×{e.height} (tỷ lệ {rules['delivery']['naming']['ratio']}), {e.fps} fps, "
        "h264/aac đúng cấu hình."
        if not bad and ratio_ok
        else " ".join(f.msg for f in bad) or f"Khung {e.width}×{e.height} không phải 9:16."
    )
    return E1Item(code="E1-FORMAT", name="Độ phân giải và tỷ lệ khung", passed=ratio_ok and not bad,
                  detail=detail)  # fmt: skip


def _loud_item(e: Edl, rules: Rules, found: Sequence[Finding], lufs: float | None,
               peak: float | None) -> E1Item:  # fmt: skip
    cfg = rules["qc"]
    bad = _blocked(found, ("T3",))
    if lufs is None or peak is None:
        quiet = (
            "Không đo âm lượng (video không tiếng: không nhúng nhạc, nhạc chèn trên app)."
            if e.no_music
            else "Không đo âm lượng (video không tiếng, theo kế hoạch)."
        )
        detail = " ".join(f.msg for f in bad) or quiet
        return E1Item(code="E1-LOUD", name="Âm lượng và đỉnh", passed=not bad, detail=detail)
    detail = (f"{lufs:.1f} LUFS (cần {e.lufs:g} ±{float(cfg['lufs_tolerance']):g}), đỉnh thật "
              f"{peak:.1f} dBTP (trần {float(cfg['max_true_peak_db']):g}); đỉnh dưới 0 dBTP nên "
              "âm thanh không bị clip.")  # fmt: skip
    return E1Item(code="E1-LOUD", name="Âm lượng và đỉnh", passed=not bad, value=round(lufs, 2),
                  limit=e.lufs, unit="LUFS", detail=detail if not bad else detail + " "
                  + " ".join(f.msg for f in bad))  # fmt: skip


def _picture_item(found: Sequence[Finding]) -> E1Item:
    blacks = [f for f in found if f.code == "T4"]
    freezes = [f for f in found if f.code == "T5"]
    detail = (f"{len(blacks)} đoạn khung đen, {len(freezes)} đoạn hình đứng yên (đứng hình chỉ "
              "cảnh báo, cần xem bằng mắt).")  # fmt: skip
    return E1Item(code="E1-PICTURE", name="Không khung đen, không đứng hình bất thường",
                  passed=not blacks, value=float(len(blacks)), limit=0.0, unit="đoạn",
                  detail=detail)  # fmt: skip


def build_e1(
    e: Edl, rules: Rules, found: Sequence[Finding], lufs: float | None, true_peak_db: float | None,
    checks: Mapping[str, E1Item],
) -> E1Report:  # fmt: skip
    """Put the T1–T5 outcome and the new checks (keys E1-DUR, E1-DUR-GAP, E1-SAFE, E1-GLYPH, C1)
    in one report, in the order the owner listed them. `found` = the findings of the QC loop."""
    items = [
        checks["E1-DUR"],
        checks["E1-DUR-GAP"],
        _format_item(e, rules, found),
        _loud_item(e, rules, found, lufs, true_peak_db),
        _picture_item(found),
        checks["E1-SAFE"],
        checks["E1-GLYPH"],
        checks["C1"],
        *([checks["E1-NEN-TANG"]] if "E1-NEN-TANG" in checks else []),  # warning only
    ]
    return E1Report(passed=all(i.passed for i in items), items=items)
