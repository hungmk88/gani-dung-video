"""Preflight: check an option combo against rules.json BEFORE any AI call (spec v2.1 §6.3).

Why: block broken combos before spending API tokens/time on a draft that is certain to be
thrown away (spec §4.3 rule 3). Chặn tổ hợp hỏng trước khi gọi AI — không tốn tiền.

Covers X1–X7 and X9–X15. X8 needs CLIP scene labels (plan stage); X16 needs language
understanding (AI stage); X17/X18 happen while snapping cuts to beats (render stage).
Pure domain code: no file or network I/O — rules are passed in.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, TypedDict

from pydantic import ValidationError

from gani_dung_video.domain.bpm_safety import check_bpm_safe
from gani_dung_video.domain.bpm_safety import message as bpm_message
from gani_dung_video.domain.options import VideoOptions

# Any: parsed rules.json; its shape is pinned by tests/test_rules.py, not by the type system.
Rules = dict[str, Any]

# Which input-error code a Pydantic error on each field maps to (others → INVALID_FACT).
_FIELD_CODES = {
    "phong_cach": "INVALID_PHONG_CACH",
    "muc_dich": "INVALID_MUC_DICH",
    "do_dai_giay": "INVALID_DO_DAI",
    "so_ban_nhap": "INVALID_SO_BAN_NHAP",
    "nhac": "INVALID_NHAC",
}


class Issue(TypedDict):
    code: str
    level: str  # block | warn | auto
    msg: str


class Result(TypedDict):
    valid: bool
    error_code: str
    error_msg: str
    errors: list[Issue]
    warnings: list[Issue]
    adjustments: list[Issue]
    scenes_needed: int | None


def scenes_needed(rules: Rules, style: str, duration: int) -> int:
    """Scenes a combo needs, from the spec §5.3.A table in rules.json.

    Why a table, not duration ÷ scene_avg_sec: the spec table is not reproducible by one
    rounding rule (TVC 15s = 11 but 45s = 35) and the CEO kept 11 (28/09/2026).

    Example:
        >>> scenes_needed({"scenes_needed": {"tvc_cat_nhanh": {"30": 23}}}, "tvc_cat_nhanh", 30)
        23
    """
    return int(rules["scenes_needed"][style][str(duration)])


def _issue(
    rules: Rules, code: str, level: str | None = None, key: str = "msg", **kw: object
) -> Issue:
    c = rules["conflicts"][code]
    return {"code": code, "level": level or c["level"], "msg": c[key].format(**kw)}


def _input_issue(rules: Rules, code: str, **kw: object) -> Issue:
    return {"code": code, "level": "block", "msg": rules["input_msgs"][code].format(**kw)}


def _pydantic_issues(rules: Rules, raw: Mapping[str, Any], err: ValidationError) -> list[Issue]:
    out: list[Issue] = []
    for e in err.errors():
        field = str(e["loc"][0]) if e["loc"] else "?"
        if e["type"] == "extra_forbidden" and len(e["loc"]) == 1:  # unknown top-level key
            out.append(_input_issue(rules, "INVALID_FIELD", field=field))
            continue
        code = _FIELD_CODES.get(field, "INVALID_FACT")
        value = raw.get(field)
        if code == "INVALID_FACT":
            out.append(_input_issue(rules, code, field=field, value=value))
        else:
            out.append(_input_issue(rules, code, value=value, allowed=_allowed(rules, code)))
    return out


def _allowed(rules: Rules, code: str) -> str:
    source = {
        "INVALID_PHONG_CACH": list(rules["styles"]),
        "INVALID_MUC_DICH": list(rules["purposes"]),
        "INVALID_DO_DAI": list(rules["durations"]),
        "INVALID_SO_BAN_NHAP": list(rules["drafts"]["allowed"]),
        "INVALID_NHAC": list(rules["music_modes"]),
    }[code]
    return ", ".join(map(str, source))


def _choice_issues(rules: Rules, o: VideoOptions) -> list[Issue]:
    """Choice values live in rules.json so a new option is a config edit, not a code edit."""
    checks = [
        ("INVALID_PHONG_CACH", o.phong_cach, list(rules["styles"])),
        ("INVALID_MUC_DICH", o.muc_dich, list(rules["purposes"])),
        ("INVALID_DO_DAI", str(o.do_dai_giay), list(rules["durations"])),
        ("INVALID_SO_BAN_NHAP", o.so_ban_nhap, list(rules["drafts"]["allowed"])),
        ("INVALID_NHAC", o.nhac, list(rules["music_modes"])),
    ]
    return [
        _input_issue(rules, code, value=value, allowed=_allowed(rules, code))
        for code, value, allowed in checks
        if value not in allowed
    ]


def _parse(rules: Rules, raw: object) -> tuple[VideoOptions | None, list[Issue]]:
    if not isinstance(raw, Mapping):
        return None, [_input_issue(rules, "INVALID_OPTIONS")]
    try:
        opts = VideoOptions.model_validate(dict(raw))
    except ValidationError as e:
        return None, _pydantic_issues(rules, raw, e)
    bad = _choice_issues(rules, opts)
    return (None, bad) if bad else (opts, [])


def _style_name(rules: Rules, style: str) -> str:
    return str(rules["styles"][style]["name"])


def _compat_issue(rules: Rules, code: str, **kw: object) -> Issue:
    return {"code": code, "level": "warn", "msg": rules["compat_msgs"][code].format(**kw)}


def _check_structure(rules: Rules, o: VideoOptions) -> list[Issue]:
    """X1, X2, X9, X10, X15 + soft compat hints — depend only on the chosen options."""
    dur, sname = o.do_dai_giay, _style_name(rules, o.phong_cach)
    pname = rules["purposes"][o.muc_dich]["name"]
    out: list[Issue] = []
    if dur > rules["styles"][o.phong_cach].get("max_duration", math.inf):
        out.append(_issue(rules, "X1"))
    if dur > rules["purposes"][o.muc_dich].get("max_duration", math.inf):
        out.append(_issue(rules, "X2"))
    sp = rules["compat_style_purpose"][o.phong_cach][o.muc_dich]
    if sp == "bad":
        out.append(_issue(rules, "X9", style=sname, purpose=pname))
    elif sp == "warn":
        out.append(_compat_issue(rules, "W_STYLE_PURPOSE", style=sname, purpose=pname))
    x10 = rules["conflicts"]["X10"]["when"]
    x10_hit = o.phong_cach in x10["styles"] and dur == x10["duration"]
    if x10_hit:
        out.append(_issue(rules, "X10", style=sname))
    # 'bad' style×duration is already blocked by X1; X10 covers its own 'warn' cells.
    if rules["compat_style_duration"][o.phong_cach][str(dur)] == "warn" and not x10_hit:
        out.append(_compat_issue(rules, "W_STYLE_DURATION", style=sname, duration=dur))
    x15 = rules["conflicts"]["X15"]["when"]
    if o.so_ban_nhap == x15["drafts"] and dur == x15["duration"]:
        out.append(_issue(rules, "X15"))
    return out


def _check_required_input(rules: Rules, o: VideoOptions) -> list[Issue]:
    """X3 (promo fields), X4 (free-style note length), X14 (music rights tick)."""
    out: list[Issue] = []
    km = o.khuyen_mai.model_dump() if o.khuyen_mai else {}
    fields = rules["purposes"][o.muc_dich].get("required_fields", [])
    missing = [f for f in fields if not str(km.get(f, "")).strip()]
    if missing:
        out.append(_issue(rules, "X3", missing=", ".join(missing)))
    min_words = rules["styles"][o.phong_cach].get("note_min_words")
    count = len((o.ghi_chu or "").split())
    if min_words and count < min_words:
        out.append(_issue(rules, "X4", min=min_words, count=count))
    if rules["music_modes"][o.nhac]["needs_rights_tick"] and o.nhac_quyen_su_dung is not True:
        out.append(_issue(rules, "X14"))
    return out


def _check_footage(rules: Rules, o: VideoOptions, need: int) -> list[Issue]:
    """X5/X6 (enough usable scenes), X7 (speech style without speech). Skipped if unknown."""
    out: list[Issue] = []
    usable = o.so_canh_dung_duoc
    if usable is not None:
        ideal = math.ceil(need * float(rules["footage"]["min_usable_ratio"]))
        if usable < need:
            out.append(_issue(rules, "X5", usable=usable, need=need, short=need - usable))
        elif usable < ideal:
            out.append(_issue(rules, "X6", usable=usable, ideal=ideal))
    if rules["styles"][o.phong_cach].get("requires_speech") and o.co_loi_noi is False:
        out.append(_issue(rules, "X7"))
    return out


def _bpm_deviation(bpm: float, lo: float, hi: float) -> float:
    if lo <= bpm <= hi:
        return 0.0
    return (lo - bpm) / lo if bpm < lo else (bpm - hi) / hi


def check_bpm(rules: Rules, bpm: float, lo: float, hi: float) -> Issue | None:
    """X12: try the track as-is, halved and doubled; warn only if all stay > limit away.

    Example:
        Sang trọng (60–85 BPM) with a 128 BPM track → auto-adjust to 64 BPM (count 2 beats as 1).
    """
    if lo <= bpm <= hi:
        return None
    candidates = [
        (bpm, "giữ nguyên"),
        (bpm / 2, "đếm 2 phách thành 1"),
        (bpm * 2, "tách 1 phách thành 2"),
    ]
    eff, how = min(candidates, key=lambda c: _bpm_deviation(c[0], lo, hi))
    kw = {"bpm": f"{bpm:g}", "lo": f"{lo:g}", "hi": f"{hi:g}"}
    if _bpm_deviation(eff, lo, hi) > float(rules["music"]["bpm_max_deviation"]):
        return _issue(rules, "X12", level="warn", key="msg_warn", **kw)
    return _issue(rules, "X12", eff=f"{eff:g}", how=how, **kw)


def _check_music(rules: Rules, o: VideoOptions) -> list[Issue]:
    """X11 (no music × music-driven style), X12 (BPM range), X13 (track shorter than video)."""
    style = rules["styles"][o.phong_cach]
    if o.nhac == "khong_nhac":
        if style.get("driver_fallback") == "grid":
            return [_issue(rules, "X11", style=style["name"], grid=style["scene_avg_sec"])]
        return []
    out: list[Issue] = []
    rng = style.get("bpm_range")
    if o.nhac_bpm is not None and rng:
        x12 = check_bpm(rules, o.nhac_bpm, float(rng[0]), float(rng[1]))
        if x12:
            out.append(x12)
    if o.nhac_giay is not None and o.nhac_giay < o.do_dai_giay:
        out.append(_issue(rules, "X13", music=f"{o.nhac_giay:g}", video=o.do_dai_giay))
    out += _check_bpm_safety(rules, o)
    return out


def _check_bpm_safety(rules: Rules, o: VideoOptions) -> list[Issue]:
    """W_BPM_BLOCK / W_BPM_CUT: the track's BPM, looked up in the measured table (a warning
    only; `bpm_override` means the person already chose to go on)."""
    if o.nhac_bpm is None or o.bpm_override:
        return []
    chk = check_bpm_safe(rules, o.phong_cach, o.do_dai_giay, o.nhac_bpm)
    code = {"BLOCKING": "W_BPM_BLOCK", "WARNING": "W_BPM_CUT"}.get(chk.level)
    if code is None:
        return []
    text = bpm_message(rules, code, o.phong_cach, o.do_dai_giay, o.nhac_bpm, chk.buildable)
    return [{"code": code, "level": "warn", "msg": text}]


def _check_version(rules: Rules, o: VideoOptions) -> list[Issue]:
    if o.rules_version is None or str(o.rules_version) == str(rules["rules_version"]):
        return []
    msg = rules["input_msgs"]["RULES_VERSION"].format(
        got=o.rules_version, want=rules["rules_version"]
    )
    return [{"code": "RULES_VERSION", "level": "warn", "msg": msg}]


def _result(issues: list[Issue], need: int | None) -> Result:
    errors = [i for i in issues if i["level"] == "block"]
    return {
        "valid": not errors,
        "error_code": errors[0]["code"] if errors else "",
        "error_msg": errors[0]["msg"] if errors else "",
        "errors": errors,
        "warnings": [i for i in issues if i["level"] == "warn"],
        "adjustments": [i for i in issues if i["level"] == "auto"],
        "scenes_needed": need,
    }


def preflight_check(options: object, rules: Rules) -> Result:
    """Check one option combo against the rules before any AI call.

    Args:
        options: Option block (spec Appendix D keys) plus optional footage facts
            (so_canh_dung_duoc, co_loi_noi, nhac_bpm, nhac_giay). Unknown facts are skipped.
        rules: Parsed config/rules.json.

    Returns:
        ``valid`` is False iff at least one BLOCK issue fired; ``error_code``/``error_msg``
        describe the first one (Vietnamese, user-facing). Warnings/adjustments never block.

    Example:
        >>> r = preflight_check({"phong_cach": "tvc_cat_nhanh", "muc_dich": "ban_hang",
        ...     "do_dai_giay": 180, "so_ban_nhap": 1, "nhac": "ai_tu_chon"}, rules)
        >>> (r["valid"], r["error_code"])
        (False, 'X1')
    """
    opts, bad = _parse(rules, options)
    if opts is None:
        return _result(bad, None)
    need = scenes_needed(rules, opts.phong_cach, opts.do_dai_giay)
    issues = (
        _check_structure(rules, opts)
        + _check_required_input(rules, opts)
        + _check_footage(rules, opts, need)
        + _check_music(rules, opts)
        + _check_version(rules, opts)
    )
    return _result(issues, need)
