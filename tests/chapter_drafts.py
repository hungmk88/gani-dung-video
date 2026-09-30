"""A valid 3-minute answer built from a plan and its candidates (phase 7c test helper).

No Claude: it walks the plan the way a careful editor would — scenes of each block taken from the
block's own candidates (first ones for draft A, last ones for draft B, so the two drafts share
nothing), sizes chosen so every block adds up exactly, four text lines per chapter and one title
per chapter. Anything the checks ask for is satisfied; tests then break one thing at a time.
"""

from __future__ import annotations

import copy
from typing import Any

from mock_footage import STYLE, mock_scenes

from gani_dung_video.domain.options import VideoOptions
from gani_dung_video.domain.pick import PickFile, eligible_candidates, rank_plan
from gani_dung_video.domain.plan import MusicInfo, Plan, compile_plan
from gani_dung_video.infrastructure.config_files import load_rules

SHIPPED = load_rules()
ALL_TAGS = {"nguoi_thao_tac", "thiet_bi", "chung_nhan", "thanh_pham", "khu_vuc", "mon_an"}
CTA = "Nhắn tin nhận báo giá sỉ"
TITLES = ["Khởi đầu sạch sẽ", "Từng mẻ giò thủ công", "Minh chứng chất lượng", "Lời hẹn với bạn"]
OPTS: dict[str, Any] = {
    "phong_cach": STYLE, "muc_dich": "gioi_thieu_xuong", "do_dai_giay": 180, "so_ban_nhap": 2,
    "nhac": "thu_vien", "nhac_file": "am_ap_84.mp3",
}  # fmt: skip


def rules_on() -> Any:
    r = copy.deepcopy(dict(SHIPPED))
    r["durations"]["180"]["enabled"] = True
    return r


def make_plan_and_pick(rules: Any, clips: int = 28) -> tuple[Plan, PickFile]:
    music = MusicInfo(track="am_ap_84.mp3", bpm=84.0, first_beat_sec=0.5, duration_sec=200.0,
                      source="library")  # fmt: skip
    plan = compile_plan(VideoOptions(**OPTS), rules, music=music, available_tags=ALL_TAGS)
    pool = eligible_candidates(mock_scenes(clips), STYLE, plan.beat_sec,
                               plan.beats_per_scene_allowed)  # fmt: skip
    blocks, missing = rank_plan(plan.blocks, pool, set(), plan.beats_per_scene_allowed,
                                rules["pick"])  # fmt: skip
    pick = PickFile(rules_version="2.0", style=STYLE, beat_sec=plan.beat_sec,
                    beats_per_scene_allowed=plan.beats_per_scene_allowed, dishes=[],
                    blocks=blocks, missing_shots=missing, similar_pairs=[])  # fmt: skip
    return plan, pick


def _reachable(n: int) -> bool:
    return n == 0 or (n >= 4 and n != 5 and n % 2 == 0)


def _take(beats: int, cands: list[Any]) -> list[tuple[Any, int]]:
    """(candidate, size) pairs adding up to exactly `beats`, sizes from {4, 6}."""
    out: list[tuple[Any, int]] = []
    left = beats
    for c in cands:
        if left == 0:
            break
        for size in (6, 4):
            if size in c.fits_beats and _reachable(left - size):
                out.append((c, size))
                left -= size
                break
    assert left == 0, "mock footage should always be enough"
    return out


def build_draft(plan: Plan, pick: PickFile, letter: str, reverse: bool) -> dict[str, Any]:
    by_role = {b.role: b.candidates for b in pick.blocks}
    used: set[str] = set()
    chosen: dict[str, list[tuple[Any, int]]] = {}
    # Blocks with the fewest candidates choose first (hook, CTA): their scenes are also offered
    # to the chapters, which have plenty of others.
    for block in sorted(plan.blocks, key=lambda b: len(by_role[b.role])):
        cands = [c for c in by_role[block.role] if c.id not in used]
        chosen[block.role] = _take(block.beats, cands[::-1] if reverse else cands)
        used |= {c.id for c, _ in chosen[block.role]}
    segments: list[dict[str, Any]] = []
    chapter_of = {b.role: b.chapter for b in plan.blocks}
    lines: dict[str, int] = {}
    for block in plan.blocks:
        for cand, size in chosen[block.role]:
            text = ""
            chap = chapter_of[block.role]
            if chap is not None and lines.get(chap, 0) < 4 and len(segments) % 2 == 0:
                lines[chap] = lines.get(chap, 0) + 1
                text = f"Dòng {lines[chap]} của {len(segments)}"
            segments.append({"block": block.role, "scene_id": cand.id, "beats": size,
                             "text": text})  # fmt: skip
    first = next(s for s in segments if s["block"] == plan.blocks[0].role)
    first["text"] = "Một ngày ở xưởng"
    last = plan.blocks[-1].role
    next(s for s in reversed(segments) if s["block"] == last)["text"] = CTA
    for c in plan.chapters:  # at least three lines per chapter
        mine = [s for s in segments if chapter_of[s["block"]] == c.id]
        blank = [s for s in mine if not s["text"]]
        short = 3 - (len(mine) - len(blank))
        for s in blank[: max(short, 0)]:
            s["text"] = f"Thêm {s['scene_id'][-6:]}"
    titles = [{"chapter": c.id, "title": TITLES[i % len(TITLES)]}
              for i, c in enumerate(plan.chapters)]  # fmt: skip
    return {"draft": letter, "label": f"Bản {letter}", "missing_shots": [], "warnings": [],
            "segments": segments, "chapter_titles": titles}  # fmt: skip


def build_answer(plan: Plan, pick: PickFile, letters: str = "AB") -> dict[str, Any]:
    return {"drafts": [build_draft(plan, pick, x, reverse=i % 2 == 1)
                       for i, x in enumerate(letters)]}  # fmt: skip
