"""Plan compiler (phase 4a, spec v2.1 §7.2): options + rules + music → numbers for the AI.

The AI never counts seconds: this module turns the chosen options into a beat budget per
block, and the AI only picks scenes and writes text inside that budget. Pure functions.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict

from gani_dung_video.domain.beats import FOLD_NAMES, beat_grid, fold_bpm, loop_info, other_folds
from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.preflight import Rules


class PlanError(ValueError):
    """The combo cannot be compiled yet; code is a key of rules.json input_msgs."""

    def __init__(self, code: str, msg: str) -> None:
        super().__init__(msg)
        self.code = code


class MusicInfo(BaseModel):
    """What is known about the chosen track (from library.json or measured)."""

    model_config = ConfigDict(frozen=True)

    track: str
    bpm: float
    first_beat_sec: float
    duration_sec: float
    source: str  # library | detected
    path: str | None = None  # where the plan step found it (allow-listed)


class PlanMusic(BaseModel):
    model_config = ConfigDict(frozen=True)

    track: str
    bpm: float  # as measured/declared
    effective_bpm: float  # after X12 folding: the tempo beats are counted at
    fold: str  # giu_nguyen | dem_2_thanh_1 | tach_1_thanh_2
    first_beat_sec: float
    audio_in_sec: float  # start the audio here so video time 0 is beat 0
    duration_sec: float
    source: str
    loop_sec: float | None = None  # X13: whole-bar loop when the track is shorter than the video
    repeats: int | None = None
    path: str | None = None  # resolved, allow-listed file (set by the plan step, used by render)


class PlanBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: str
    beats: int
    required_tags: list[str] = []
    text: str | None = None
    chapter: str | None = None  # 3-minute videos: the chapter this sub-block belongs to


class PlanChapter(BaseModel):
    """A chapter of a 3-minute video (phase 7a): its sub-blocks, where it starts, its title."""

    model_config = ConfigDict(frozen=True)

    id: str
    role: str  # the purpose block it comes from (required tags, candidates)
    start_beat: int
    beats: int
    title_beats: int  # the title is drawn over the first beats; no time is added
    blocks: list[str]  # roles of its sub-blocks, in order


class Plan(BaseModel):
    """Content of edit/plan.json (spec §7.2)."""

    rules_version: str
    options: dict[str, Any]  # Any: echo of the chosen options (str/int values)
    driver: str  # music | grid | speech (speech: cuts follow the voice, beats place B-roll)
    music: PlanMusic | None
    beat_sec: float
    target_beats: int
    planned_beats: int
    beats_per_scene_allowed: list[int]
    blocks: list[PlanBlock]
    chapters: list[PlanChapter] = []  # empty below 180 s
    text_rules: dict[str, Any]  # Any: the style's text rules from rules.json, copied as they are
    draft_strategy: list[str]
    warnings: list[str] = []
    adjustments: list[str] = []
    trim_to_sec: float | None = None  # music: the beats run past the chosen length; cut here
    trim_fade_sec: float = 0.0  # picture and sound fade out over the last seconds of the cut


def beats_of(plan: Plan) -> list[float]:
    """Beat boundaries of the whole video, in video time (0 = first beat)."""
    return beat_grid(plan.beat_sec, plan.planned_beats)


def natural_sec(plan: Plan) -> float:
    """What the planned beats add up to, before any cut at the chosen length."""
    return plan.planned_beats * plan.beat_sec


def planned_sec(plan: Plan) -> float:
    """Length of the video content (without the end card) that the plan promises."""
    if plan.trim_to_sec is not None:
        return plan.trim_to_sec
    return natural_sec(plan)


_TRIM_POLICY = re.compile(r"fade_(\d+(?:\.\d+)?)s")
_EPS = 1e-9


def trim_policy(rules: Rules) -> tuple[float, float]:
    """(fade seconds, threshold seconds) of the music-mode trim; fade 0 = trimming is off.

    rules.qc.duration_trim_policy is "none" or "fade_<seconds>s" (e.g. "fade_0.5s").

    Raises:
        PlanError: the policy text or the threshold is not valid.
    """
    cfg = rules["qc"]
    text, thr = str(cfg["duration_trim_policy"]), float(cfg["duration_trim_threshold_sec"])
    if thr < 0:
        raise PlanError("PLAN_RULES", f"rules.json qc.duration_trim_threshold_sec = {thr:g} < 0.")
    if text == "none":
        return 0.0, thr
    m = _TRIM_POLICY.fullmatch(text)
    if m is None or float(m.group(1)) <= 0:
        raise PlanError("PLAN_RULES", f"rules.json qc.duration_trim_policy = '{text}': dùng "
                                      "'none' hoặc 'fade_<giây>s', ví dụ 'fade_0.5s'.")  # fmt: skip
    return float(m.group(1)), thr


# ─────────────── beat budget per block ───────────────


def _reachable(allowed: Sequence[int], limit: int) -> list[int]:
    """Scene-count totals a block can actually have: sums of allowed beats per scene."""
    ok = [False] * (limit + 1)
    ok[0] = True
    for n in range(1, limit + 1):
        ok[n] = any(n >= a and ok[n - a] for a in allowed)
    return [n for n in range(1, limit + 1) if ok[n]]


def snap_size(x: float, allowed: Sequence[int]) -> int:
    """Round x beats to the nearest total scenes can add up to (ties up, at least one scene).

    Example: with 4- or 6-beat scenes, 4.6 → 4 and 11.5 → 12 (spec §7.2).
    """
    cands = _reachable(allowed, math.ceil(max(x, 0.0)) + 2 * max(allowed))
    return min(cands, key=lambda n: (abs(n - x), -n))


def _floor_size(x: float, allowed: Sequence[int]) -> int:
    """Largest total ≤ x (but at least one scene)."""
    fits = [n for n in _reachable(allowed, max(math.floor(x), 0) + max(allowed)) if n <= x]
    return max(fits) if fits else min(allowed)


def _settle(sizes: list[int], target: int, allowed: Sequence[int]) -> list[int]:
    """Push the rounding remainder into one content block (hook/cta only if nothing else).

    The longest content block is tried first (spec §7.2); if it cannot absorb the exact
    remainder (e.g. 12 - 2 = 10 is not a sum of 6/8), the next longest is tried, then the
    closest total wins.
    """
    diff = target - sum(sizes)
    if diff == 0:
        return sizes
    content = range(1, len(sizes) - 1) if len(sizes) > 2 else range(len(sizes))
    order = [*sorted(content, key=lambda i: (-sizes[i], i)), 0, len(sizes) - 1]
    best: list[int] | None = None
    for k in dict.fromkeys(order):
        out = list(sizes)
        out[k] = snap_size(sizes[k] + diff, allowed)
        if sum(out) == target:
            return out
        if best is None or abs(target - sum(out)) < abs(target - sum(best)):
            best = out
    return best if best is not None else sizes


def allocate_blocks(
    ratios: Sequence[tuple[str, float]], target: int, allowed: Sequence[int]
) -> list[tuple[str, int]]:
    """Beats per block = ratio × target rounded to what scenes can sum to; remainder dumped in
    the longest content block. The total can miss target by less than one scene."""
    roles = [r for r, _ in ratios]
    sizes = [snap_size(ratio * target, allowed) for _, ratio in ratios]
    return list(zip(roles, _settle(sizes, target, allowed), strict=True))


def _even_parts(total: int, parts: int, reach: Sequence[int]) -> list[int]:
    """`parts` reachable sizes adding up to `total`, closest to equal (least squares), or []."""
    mean = total / parts
    best: list[dict[int, tuple[float, int]]] = [{} for _ in range(parts + 1)]
    best[0][0] = (0.0, 0)
    for i in range(1, parts + 1):
        for prev, (cost, _) in best[i - 1].items():
            for r in reach:
                if prev + r > total:
                    break
                c = cost + (r - mean) ** 2
                if prev + r not in best[i] or c < best[i][prev + r][0]:
                    best[i][prev + r] = (c, r)
    if total not in best[parts]:
        return []
    out, left = [], total
    for i in range(parts, 0, -1):
        r = best[i][left][1]
        out.append(r)
        left -= r
    return out[::-1]


def split_exact(total: int, parts: int, allowed: Sequence[int]) -> list[int]:
    """Cut `total` beats into `parts` sizes that are each a sum of allowed scene sizes, add up
    exactly and differ as little as possible; fewer parts when `total` is too small."""
    reach = _reachable(allowed, total)
    parts = max(1, min(parts, total // min(allowed)))
    while parts > 1:
        got = _even_parts(total, parts, reach)
        if got:
            return got
        parts -= 1
    return [total]


# ─────────────── compile ───────────────


def _block_specs(
    purpose: Mapping[str, Any], dur_cfg: Mapping[str, Any]
) -> tuple[list[tuple[str, float]], dict[str, list[str]]]:
    """Blocks and their required tags; 15 s videos merge everything between hook and cta."""
    blocks = [(str(r), float(x)) for r, x in purpose["blocks"]]
    tags: dict[str, list[str]] = {k: list(v) for k, v in purpose.get("required_tags", {}).items()}
    if dur_cfg.get("blocks_merge"):
        mid = blocks[1:-1]
        tags["main"] = list(dict.fromkeys(k for r, _ in mid for k in tags.get(r, [])))
        blocks = [blocks[0], ("main", sum(x for _, x in mid)), blocks[-1]]
    return blocks, tags


def _music_setup(
    o: VideoOptions, rules: Rules, style: Mapping[str, Any], music: MusicInfo | None,
    allowed: Sequence[int],
) -> tuple[str, float, PlanMusic | None, list[str]]:  # fmt: skip
    """(driver, beat_sec, music, warnings): the track's beats, or the virtual grid."""
    grid_sec = float(style["scene_avg_sec"]) / min(allowed)  # shortest scene = average scene
    msgs = rules["compat_msgs"]
    if o.nhac == "khong_nhac":
        warn = [] if style.get("driver_fallback") == "grid" else [
            "W_GRID_NO_MUSIC: " + msgs["W_GRID_NO_MUSIC"].format(grid=f"{grid_sec:g}")
        ]  # fmt: skip
        return "grid", grid_sec, None, warn
    if music is None:
        return (
            "grid",
            grid_sec,
            None,
            ["W_NO_BEAT: " + msgs["W_NO_BEAT"].format(grid=f"{grid_sec:g}")],
        )
    rng = style.get("bpm_range")
    eff, fold = fold_bpm(music.bpm, (float(rng[0]), float(rng[1])) if rng else None,
                         float(rules["music"]["bpm_max_deviation"]))  # fmt: skip
    # loop_sec / repeats are set by compile_plan once the rendered length is known.
    pm = PlanMusic(track=music.track, bpm=music.bpm, effective_bpm=eff, fold=fold,
                   first_beat_sec=music.first_beat_sec, audio_in_sec=music.first_beat_sec,
                   duration_sec=music.duration_sec, source=music.source)  # fmt: skip
    return "music", 60.0 / eff, pm, []


def _with_loop(pm: PlanMusic, rules: Rules, content_sec: float) -> PlanMusic:
    """The track's loop for a video whose content lasts `content_sec`: the music has to reach
    the end of the end card too (X13), counted from the first beat."""
    need = content_sec + float(rules["render"]["end_card_sec"])
    loop = loop_info(pm.duration_sec, pm.first_beat_sec, pm.bpm, need,
                     int(rules["music"]["loop_unit_beats"]))  # fmt: skip
    return pm.model_copy(update={"loop_sec": loop[0] if loop else None,
                                 "repeats": loop[1] if loop else None})  # fmt: skip


def _chapter_blocks(
    o: VideoOptions, purpose: Mapping[str, Any], dur_cfg: Mapping[str, Any], beat_sec: float,
    allowed: Sequence[int], target: int, cta: str,
) -> tuple[list[PlanBlock], list[PlanChapter]]:  # fmt: skip
    """Hook + chapters + CTA of a 3-minute video (design GD7 §2): the hook and the CTA get real
    seconds, the chapters share the rest by weight, each chapter is cut into sub-blocks."""
    lay = dur_cfg["layouts"].get(o.muc_dich)
    if lay is None:
        raise PlanError("PLAN_CHAPTERS", f"Mục đích '{o.muc_dich}' không có bố cục 3 phút.")
    tags: Mapping[str, list[str]] = purpose.get("required_tags", {})
    hook = _floor_size(float(dur_cfg["hook_sec"]) / beat_sec, allowed)
    end = snap_size(float(dur_cfg["cta_sec"]) / beat_sec, allowed)
    rest, weights = target - hook - end, [float(w) for _, _, w in lay["chapters"]]
    sizes = [hook, *(snap_size(w / sum(weights) * rest, allowed) for w in weights), end]
    sizes = _settle(sizes, target, allowed)
    title = max(1, math.floor(float(dur_cfg["chapter_title_sec"]) / beat_sec + 0.5))
    blocks = [PlanBlock(role=lay["hook"], beats=sizes[0], required_tags=tags.get(lay["hook"], []))]
    chapters: list[PlanChapter] = []
    for (cid, source, _), beats in zip(lay["chapters"], sizes[1:-1], strict=True):
        subs = split_exact(beats, round(beats / int(dur_cfg["sub_block_beats"])), allowed)
        roles = [f"{cid}#{k}" for k in range(1, len(subs) + 1)]
        blocks += [PlanBlock(role=r, beats=n, required_tags=list(tags.get(source, [])), chapter=cid)
                   for r, n in zip(roles, subs, strict=True)]  # fmt: skip
        start = sum(b.beats for b in blocks) - sum(subs)
        chapters.append(PlanChapter(id=cid, role=source, start_beat=start, beats=sum(subs),
                                    title_beats=title, blocks=roles))  # fmt: skip
    blocks.append(PlanBlock(role=lay["cta"], beats=sizes[-1], text=cta,
                            required_tags=list(tags.get(lay["cta"], []))))  # fmt: skip
    return blocks, chapters


def _short_blocks(
    o: VideoOptions, purpose: Mapping[str, Any], dur_cfg: Mapping[str, Any], beat_sec: float,
    allowed: Sequence[int], target: int, cta: str,
) -> list[PlanBlock]:  # fmt: skip
    """The blocks of a 15–60 s video: the purpose's ratios, rounded to what scenes can sum to."""
    ratios, tags = _block_specs(purpose, dur_cfg)
    roles = [r for r, _ in ratios]
    sizes = [snap_size(x * target, allowed) for _, x in ratios]
    if dur_cfg.get("blocks_merge"):  # fixed hook/cta lengths of a 15 s video
        sizes[-1] = snap_size(float(dur_cfg["cta_sec"]) / beat_sec, allowed)
        sizes[0] = min(sizes[0], _floor_size(float(dur_cfg["hook_max_sec"]) / beat_sec, allowed))
    sizes = _settle(sizes, target, allowed)
    return [
        PlanBlock(role=r, beats=n, required_tags=tags.get(r, []),
                  text=cta if i == len(roles) - 1 else None)
        for i, (r, n) in enumerate(zip(roles, sizes, strict=True))
    ]  # fmt: skip


class _Fit(NamedTuple):
    blocks: list[PlanBlock]
    chapters: list[PlanChapter]
    beat_sec: float
    notes: list[str]
    trim_to_sec: float | None
    trim_fade_sec: float


def _fit_to_chosen(
    chosen: float, driver: str, beat_sec: float, allowed: Sequence[int], target: int,
    build: Callable[[int], tuple[list[PlanBlock], list[PlanChapter]]],
    first: tuple[list[PlanBlock], list[PlanChapter]], policy: tuple[float, float],
) -> _Fit:  # fmt: skip
    """Make the planned length equal the chosen length (decision 13/10/2026, "hướng B").

    Grid (no music): the beat is a virtual number, so it is stretched until the planned beats
    add up to exactly the chosen seconds; the beat count (the rhythm) is untouched.

    Music: the track's tempo is fixed, so the planner picks, among the beat totals it can
    build, the one that needs the least cutting. A plan within `threshold` of the chosen length
    is left alone; one more than `threshold` longer is cut at the chosen length (the render
    fades picture and sound over `fade` seconds). Speech videos follow the voice and are not
    touched here.
    """
    blocks, chapters = first
    fade, thr = policy
    total = sum(b.beats for b in blocks)
    notes: list[str] = []
    if driver == "grid":
        stretched = chosen / total
        if abs(stretched - beat_sec) > _EPS:
            notes.append(
                f"Co giãn lưới ảo: 1 phách {beat_sec:.3f}s → {stretched:.3f}s "
                f"({(stretched / beat_sec - 1) * 100:+.1f}%) để video đúng {chosen:g}s."
            )
        return _Fit(blocks, chapters, stretched, notes, None, 0.0)
    if driver != "music" or fade <= 0:
        return _Fit(blocks, chapters, beat_sec, notes, None, 0.0)
    total_first = total
    blocks, chapters = _pick_music_plan(chosen, beat_sec, allowed, target, build, first, thr)
    total = sum(b.beats for b in blocks)
    if total != total_first:
        notes.append(
            f"Nhạc: lập lại kế hoạch {total} phách ({total * beat_sec:.2f}s) thay cho "
            f"{total_first} phách ({total_first * beat_sec:.2f}s) để ít phải cắt nhất."
        )
    if total * beat_sec > chosen + thr + _EPS:
        notes.append(
            f"Nhạc: kế hoạch {total * beat_sec:.2f}s dài hơn {chosen:g}s quá {thr:g}s, "
            f"cắt ở {chosen:g}s và mờ dần {fade:g}s cuối."
        )
        return _Fit(blocks, chapters, beat_sec, notes, chosen, fade)
    return _Fit(blocks, chapters, beat_sec, notes, None, 0.0)


def _pick_music_plan(
    chosen: float, beat_sec: float, allowed: Sequence[int], target: int,
    build: Callable[[int], tuple[list[PlanBlock], list[PlanChapter]]],
    first: tuple[list[PlanBlock], list[PlanChapter]], thr: float,
) -> tuple[list[PlanBlock], list[PlanChapter]]:  # fmt: skip
    """Among the plans the tempo allows (different beat totals), the one that needs the least
    cutting: first one within `thr` of the chosen length (closest wins), else the shortest one
    that is longer (it gets cut), else the longest one that is shorter."""
    width = 2 * max(allowed) + 2
    found = {sum(b.beats for b in first[0]): first}
    for t in range(max(1, target - width), target + width + 1):
        if t != target:
            blocks, chapters = build(t)
            found.setdefault(sum(b.beats for b in blocks), (blocks, chapters))

    def rank(n: int) -> tuple[int, float, float]:
        dev = n * beat_sec - chosen
        if abs(dev) <= thr + _EPS:
            return (0, abs(dev), -float(n))
        return (1, dev, 0.0) if dev > 0 else (2, -dev, 0.0)

    return found[min(found, key=rank)]


def speech_driven(style: Mapping[str, Any], co_loi_noi: bool | None, chapters: bool) -> bool:
    """Whether the voice cuts the video: a speech-driven style (Bán hàng có lời), or a style
    with `driver_if_speech` (Review món ăn, Kể chuyện nghề, 26/10/2026) on footage known to have
    speech. A 3-minute video of such a style stays on music: speech chapters wait for 7f."""
    if style["driver"] == "speech":
        return True
    return style.get("driver_if_speech") == "speech" and co_loi_noi is True and not chapters


def _check_chapters_open(o: VideoOptions, rules: Rules, style: Mapping[str, Any],
                         dur_cfg: Mapping[str, Any]) -> None:  # fmt: skip
    """Raise PlanError while 3-minute videos are switched off or need the speech driver (7f)."""
    if not dur_cfg.get("chapters"):
        return
    if not dur_cfg.get("enabled"):
        raise PlanError("PLAN_CHAPTERS", rules["input_msgs"]["PLAN_CHAPTERS"])
    if style["driver"] == "speech":
        raise PlanError("PLAN_CHAPTERS_SPEECH", rules["input_msgs"]["PLAN_CHAPTERS_SPEECH"])


def compile_plan(
    options: VideoOptions, rules: Rules, *, music: MusicInfo | None,
    available_tags: Collection[str],
) -> Plan:  # fmt: skip
    """Compile the option combo into plan.json (spec §7.2).

    Args:
        music: the chosen track, or None for "no music" / a track whose beats could not be found
            (then the virtual grid is used and a warning says why).
        available_tags: CLIP labels present on usable segments; a required tag missing from
            it raises the X8 warning.

    Raises:
        PlanError: 3-minute video while it is switched off (`durations.180.enabled`), or in the
            speech-driven style (phase 7f).
    """
    o = options
    style, purpose = rules["styles"][o.phong_cach], rules["purposes"][o.muc_dich]
    dur_cfg = rules["durations"][str(o.do_dai_giay)]
    _check_chapters_open(o, rules, style, dur_cfg)
    # 5c: speech cuts; beats only place B-roll
    speech = speech_driven(style, o.co_loi_noi, bool(dur_cfg.get("chapters")))
    allowed = [int(n) for n in style["broll_beats" if speech else "beats_allowed"]]
    driver, beat_sec, pm, warnings = _music_setup(o, rules, style, music, allowed)
    if speech:
        driver, warnings = "speech", [w for w in warnings if not w.startswith("W_GRID_NO_MUSIC")]
    cta = str(purpose["cta"]).replace("[ngày]", o.khuyen_mai.thoi_han if o.khuyen_mai else "")
    text_rules = dict(style.get("text", {}))
    chosen, pct = float(o.do_dai_giay), float(rules["qc"]["duration_plan_gap_pct"])

    def plan_at(nominal: float) -> tuple[int, _Fit]:
        """Blocks and fit when beats are counted every `nominal` seconds."""

        def build(t: int) -> tuple[list[PlanBlock], list[PlanChapter]]:
            if dur_cfg.get("chapters"):
                return _chapter_blocks(o, purpose, dur_cfg, nominal, allowed, t, cta)
            return _short_blocks(o, purpose, dur_cfg, nominal, allowed, t, cta), []

        t = max(1, math.floor(chosen / nominal + 0.5))
        return t, _fit_to_chosen(chosen, driver, nominal, allowed, t, build, build(t),
                                 trim_policy(rules))  # fmt: skip

    def gap(f: _Fit) -> float:
        return abs(sum(b.beats for b in f.blocks) * f.beat_sec - chosen)

    if dur_cfg.get("chapters"):
        text_rules.pop("per_block", None)  # a chapter is long: count the lines per chapter
        text_rules.update(dur_cfg.get("text", {}))
    target, fit = plan_at(beat_sec)
    if driver == "music" and pm is not None and gap(fit) > pct * chosen + _EPS:
        # 14/10/2026: the X12 count chosen for the feel cannot reach the length (Sang trọng at
        # 100 BPM counted as 50: one 7.2 s scene per block, 21.6 s for 15 s). Another count
        # still inside the style's tempo tolerance may: keep the one closest to the length.
        rng = style.get("bpm_range")
        span = (float(rng[0]), float(rng[1])) if rng else None
        for eff, mode in other_folds(pm.bpm, span, pm.fold,
                                     float(rules["music"]["bpm_max_deviation"])):  # fmt: skip
            t2, f2 = plan_at(60.0 / eff)
            if gap(f2) < gap(fit) - _EPS:
                note = (f"Nhạc: đếm phách ở {eff:g} BPM ({FOLD_NAMES.get(mode, mode)}) thay vì "
                        f"{pm.effective_bpm:g} BPM ({FOLD_NAMES.get(pm.fold, pm.fold)}) để kế "
                        f"hoạch gần {chosen:g}s hơn.")  # fmt: skip
                target, fit = t2, f2._replace(notes=[note, *f2.notes])
                pm = pm.model_copy(update={"effective_bpm": eff, "fold": mode})
    blocks, chapters, beat_sec = fit.blocks, fit.chapters, fit.beat_sec
    missing = list(dict.fromkeys(t for b in blocks for t in b.required_tags
                                 if t not in available_tags))  # fmt: skip
    if missing:
        names = rules["pick"]["tag_names"]  # 'nguoi_thao_tac' reads 'Công nhân thao tác'
        warnings.append("X8: " + rules["conflicts"]["X8"]["msg"].format(
            tags=", ".join(str(names.get(t, t)) for t in missing)))  # fmt: skip
    natural = sum(b.beats for b in blocks) * beat_sec
    if pm is not None:  # what is rendered: the chosen length when trimmed, else the plan itself
        pm = _with_loop(pm, rules, fit.trim_to_sec if fit.trim_to_sec is not None else natural)
    if driver == "music" and natural > o.do_dai_giay * (1 + pct) + _EPS:
        warnings.append("W_DUR_GAP: " + rules["compat_msgs"]["W_DUR_GAP"].format(
            plan=f"{natural:.1f}", chosen=o.do_dai_giay, pct=f"{pct * 100:g}"))  # fmt: skip
    return Plan(
        rules_version=str(rules["rules_version"]),
        options={"style": o.phong_cach, "purpose": o.muc_dich, "duration": o.do_dai_giay,
                 "drafts": o.so_ban_nhap, "music": o.nhac},
        driver=driver, music=pm, beat_sec=beat_sec, target_beats=target,
        planned_beats=sum(b.beats for b in blocks), beats_per_scene_allowed=allowed,
        blocks=blocks, chapters=chapters, text_rules=text_rules,
        draft_strategy=list(rules["drafts"]["strategy"][str(o.so_ban_nhap)]),
        warnings=warnings, adjustments=fit.notes, trim_to_sec=fit.trim_to_sec,
        trim_fade_sec=fit.trim_fade_sec,
    )  # fmt: skip
