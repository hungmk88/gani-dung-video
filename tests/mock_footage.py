"""Mock footage for the long-video phases (GD7, decision Q5 of 01/10/2026): no video files.

28 clips × 3 segments look like a real workshop shoot as far as the pick and the feasibility
gate can tell: every segment is long enough for 4 (some for 6) beats, the three segments of a
clip are far apart (>= 3 s), labels cycle through the tags the purposes ask for, aesthetics vary
deterministically. Used by tests; it costs 0đ and no decoding.
"""

from __future__ import annotations

import json
from pathlib import Path

from gani_dung_video.domain.scenes import Scene, ScenesFile, Segment

STYLE = "ke_chuyen_nghe"
TAGS = ("nguoi_thao_tac", "thiet_bi", "chung_nhan", "thanh_pham", "khu_vuc", "mon_an")


def mock_scenes(
    clips: int = 28, per_clip: int = 3, style: str = STYLE, tags: tuple[str, ...] = TAGS
) -> list[Scene]:
    """`clips` scenes (one per clip), each with `per_clip` well-separated segments."""
    scenes: list[Scene] = []
    for i in range(clips):
        segs = []
        for k in range(per_clip):
            start = 12.0 * k  # 12 s apart: far more than the 3 s gap V4 asks for
            dur = (3.6, 4.8, 5.0)[(i + k) % 3]  # 4 beats, or 4–6 beats for two thirds
            segs.append(Segment(start=start, end=start + dur, peak=start + dur / 3,
                                aesthetic=4.0 + ((i * 7 + k * 3) % 10) / 5.0,
                                labels=[tags[(i * per_clip + k) % len(tags)]]))  # fmt: skip
        scenes.append(Scene(clip=f"clip{i:02d}.mp4", index=0, start=0.0, end=12.0 * per_clip,
                            sharpness=100, motion="tinh", jitter=0, consistency=1,
                            segments={style: segs}))  # fmt: skip
    return scenes


def write_mock_folder(folder: Path, clips: int = 28, style: str = STYLE) -> Path:
    """edit/scenes.json for `clips` mock clips; returns the folder."""
    scenes = mock_scenes(clips, style=style)
    sf = ScenesFile(folder=str(folder), rules_version="2.0", scenes=scenes,
                    usable_by_style={style: sum(len(s.segments[style]) for s in scenes)},
                    similar=[])  # fmt: skip
    (folder / "edit").mkdir(parents=True, exist_ok=True)
    (folder / "edit" / "scenes.json").write_text(sf.model_dump_json(), encoding="utf-8")
    return folder


def write_options(folder: Path, **extra: object) -> Path:
    opts = {"phong_cach": STYLE, "muc_dich": "gioi_thieu_xuong", "do_dai_giay": 180,
            "so_ban_nhap": 2, "nhac": "khong_nhac", **extra}  # fmt: skip
    path = folder / "o.json"
    path.write_text(json.dumps(opts), encoding="utf-8")
    return path
