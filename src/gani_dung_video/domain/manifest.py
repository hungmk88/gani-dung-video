"""E2 + E4 — manifest.json of one video: how it was made and what it cost (Dev Package 03, T-E).

Written next to every rendered video so a complaint can be traced and the same video rebuilt:
the option combo, the hashes of plan.json / rules.json / glossary.json, the models, the source
files, the E1 results and the real cost. Pure code: the caller reads the files and the clock.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from gani_dung_video.domain.delivery_checks import E1Report
from gani_dung_video.domain.review import ReviewBlock

# 2 (T-F): adds `review`. A version 1 file has no such key and still loads (review = None).
MANIFEST_VERSION = 2


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CostFacts(BaseModel):
    """E4. Tokens and money are read from edit/ai_log.jsonl (the real bill), the time is the
    wall clock of the render. The drafts of one run are written by one Claude answer, so a
    video carries its share (each round's bill split evenly between the drafts it wrote)."""

    model_config = ConfigDict(frozen=True)

    source: str  # "ai_log.jsonl" or why there is no AI bill (drafts written by hand)
    ai_rounds: int = 0  # Claude rounds of the run that wrote this draft
    ai_run_input_tokens: int = 0
    ai_run_output_tokens: int = 0
    ai_run_cost_vnd: float = 0.0
    ai_share_input_tokens: float = 0.0
    ai_share_output_tokens: float = 0.0
    ai_share_cost_vnd: float = 0.0
    render_sec: float  # render + QC (+ Whisper listen for speech finals) of this video


class Manifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest_version: int = MANIFEST_VERSION
    created_at: str  # ISO 8601 UTC
    video: str  # the working file in edit/render/<draft>/
    delivered_as: str | None  # E3 name in delivery/<campaign>/drafts, None when blocked
    draft: str
    platform: str = ""  # tiktok | facebook (14/10/2026); "" = rendered without a profile
    no_music: bool = False  # exported without the plan's music (16/10/2026)
    label: str
    resolution: str  # "1080x1920" or the 540p preview
    # Seconds the content length (file minus end card) is off the chosen length / the plan's
    # length; negative = shorter. Both are always written, whatever E1-DUR is measured against.
    deviation_vs_chosen: float | None
    deviation_vs_plan: float | None
    options: Mapping[str, str | int]  # the option combo as compiled into plan.json
    plan_sha256: str
    rules_version: str
    rules_sha256: str
    glossary_sha256: str
    models: Mapping[str, str | None]  # ai = Claude model, asr = Whisper model (speech finals)
    source_files: list[str]  # footage and music actually on the timeline
    e1: E1Report
    cost: CostFacts
    warnings: list[str] = []
    # T-F: where a delivered FINAL video stands with its reviewer. None for a preview, a
    # blocked video, and files written before T-F (see application/review_service).
    review: ReviewBlock | None = None


def last_run(
    entries: Sequence[Mapping[str, Any]],
    mode: str,  # Any: free-form ai_log.jsonl lines
) -> list[Mapping[str, Any]]:
    """The ai_log lines of the latest run of `mode` ("music" | "speech"): from the last line
    with attempt 1 to the end. The log is append-only, so older runs stay above it."""
    mine = [x for x in entries if x.get("mode", "music") == mode]
    start = max((i for i, x in enumerate(mine) if x.get("attempt") == 1), default=0)
    return mine[start:]


def ai_cost(
    entries: Sequence[Mapping[str, Any]],
    mode: str,
    letter: str,  # Any: ai_log.jsonl lines
) -> dict[str, Any]:  # Any: keyword arguments of CostFacts, of mixed types
    """Fields of CostFacts for the AI part of draft `letter`.

    Args:
        entries: parsed lines of ai_log.jsonl (any order of modes).
        mode: "speech" for speech-driven styles, "music" for the others.
        letter: the draft ("A", "B", "C").
    """
    run = last_run(entries, mode)
    if not run:
        return {"source": "không có ai_log.jsonl (bản nháp ghép tay, chưa gọi Claude)"}
    out: dict[str, Any] = {"source": "ai_log.jsonl", "ai_rounds": len(run)}
    tok_in = sum(int(x.get("input_tokens") or 0) for x in run)
    tok_out = sum(int(x.get("output_tokens") or 0) for x in run)
    cost = sum(float(x.get("cost_vnd") or 0.0) for x in run)
    out.update(ai_run_input_tokens=tok_in, ai_run_output_tokens=tok_out,
               ai_run_cost_vnd=round(cost, 2))  # fmt: skip
    share_in = share_out = share_cost = 0.0
    for x in run:
        letters = list(x.get("drafts") or [])
        if letter in letters:
            n = len(letters)
            share_in += int(x.get("input_tokens") or 0) / n
            share_out += int(x.get("output_tokens") or 0) / n
            share_cost += float(x.get("cost_vnd") or 0.0) / n
    out.update(ai_share_input_tokens=round(share_in, 1), ai_share_output_tokens=round(share_out, 1),
               ai_share_cost_vnd=round(share_cost, 2))  # fmt: skip
    return out
