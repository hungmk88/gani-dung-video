"""Shared fixtures for the T-F tests: a delivered tree on disk and a render job in a queue.

No ffmpeg: the tree is built the way delivery_service leaves it (working manifest, metadata
manifest, the E3 file in drafts/) and the job is a real row of a real queue.db.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from gani_dung_video.domain.delivery_checks import E1Item, E1Report
from gani_dung_video.domain.jobs import Job
from gani_dung_video.domain.manifest import CostFacts, Manifest
from gani_dung_video.domain.review import ReviewBlock, stamp
from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.queue_db import NewJob, QueueDb

RULES = load_rules()
NAME = "GANI_gio-lua_tvc-cat-nhanh_ban-hang_15s_9x16_v1_20261015.mp4"
VIDEO = b"0123456789" * 100
T_RENDER = datetime(2026, 10, 15, 8, 0, 0, tzinfo=UTC)  # the render finished at this moment


@dataclass
class Tree:
    db: QueueDb
    folder: Path
    root: Path
    job: Job

    @property
    def meta(self) -> Path:
        return self.root / "metadata" / f"{Path(NAME).stem}.manifest.json"

    @property
    def working(self) -> Path:
        return self.folder / "edit" / "render" / "A" / "manifest.json"

    def read(self, path: Path | None = None) -> Manifest:
        return Manifest.model_validate_json((path or self.meta).read_text(encoding="utf-8"))

    def events(self) -> list[tuple[str, str]]:
        return [(e.level, e.text) for e in self.db.events(self.job.id)]


def manifest(
    *, resolution: str = "1080x1920", passed: bool = True, review: ReviewBlock | None,
    delivered_as: str | None = NAME, extra: list[E1Item] | None = None,
) -> Manifest:  # fmt: skip
    items = [
        E1Item(code="E1-DUR", name="Độ dài", passed=True, detail="đúng"),
        E1Item(code="C1", name="Cụm cấm", passed=True, detail="sạch"),
        *(extra or []),
    ]
    return Manifest(
        created_at=T_RENDER.isoformat(timespec="seconds"), video="video_A_1080x1920.mp4",
        delivered_as=delivered_as, draft="A", label="Tối ưu", resolution=resolution,
        deviation_vs_chosen=0.0, deviation_vs_plan=0.0, options={"style": "tvc_cat_nhanh"},
        plan_sha256="0" * 64, rules_version="2.0", rules_sha256="0" * 64, glossary_sha256="0" * 64,
        models={"ai": None, "asr": None}, source_files=["a.mp4"],
        e1=E1Report(passed=passed and all(i.passed for i in items), items=items),
        cost=CostFacts(source="x", render_sec=1.0), review=review,
    )  # fmt: skip


def build(tmp_path: Path, *, final: bool = True, review: ReviewBlock | str | None = "pending",
          resolution: str = "1080x1920", extra: list[E1Item] | None = None,
          with_file: bool = True, delivered_as: str | None = NAME) -> Tree:  # fmt: skip
    folder = tmp_path / "xuong"
    root = folder / "edit" / "delivery" / "xuong"
    for d in ("raw", "drafts", "final", "metadata"):
        (root / d).mkdir(parents=True)
    (folder / "edit" / "render" / "A").mkdir(parents=True)
    if review == "pending":
        review = ReviewBlock(status="pending", requested_at=stamp(T_RENDER))
    m = manifest(resolution=resolution, review=review if isinstance(review, ReviewBlock) else None,
                 extra=extra, delivered_as=delivered_as)  # fmt: skip
    text = m.model_dump_json(indent=1)
    (folder / "edit" / "render" / "A" / "manifest.json").write_text(text, encoding="utf-8")
    if delivered_as:
        (root / "metadata" / f"{Path(delivered_as).stem}.manifest.json").write_text(
            text, encoding="utf-8")  # fmt: skip
        if with_file:
            (root / "drafts" / delivered_as).write_bytes(VIDEO)
    db = QueueDb(tmp_path / "q" / "queue.db")
    t = T_RENDER.timestamp()
    jid = db.enqueue(NewJob(kind="render", project_dir=str(folder), options_file="o", draft="A",
                            final=final, max_attempts=1), t - 100)  # fmt: skip
    claimed = db.claim(t - 60, 1)
    assert claimed is not None and claimed.id == jid
    db.finish(jid, t + 1, "completed", "passed", 0, None, None)
    job = db.get(jid)
    assert job is not None
    return Tree(db, folder, root, job)


def now_s(minutes: float = 10) -> float:
    return (T_RENDER + timedelta(minutes=minutes)).timestamp()
