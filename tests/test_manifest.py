"""Tests (T-E, E2/E4): domain/manifest.py — which Claude bill belongs to which video."""

from __future__ import annotations

from typing import Any

import pytest

from gani_dung_video.domain.manifest import CostFacts, ai_cost, last_run, sha256_of


def line(attempt: int, drafts: list[str], tok_in: int, tok_out: int, cost: float,
         mode: str = "music") -> dict[str, Any]:  # fmt: skip
    return {"mode": mode, "attempt": attempt, "drafts": drafts, "input_tokens": tok_in,
            "output_tokens": tok_out, "cost_vnd": cost}  # fmt: skip


def test_no_log_is_said_plainly_not_zero_dressed_up_as_a_bill() -> None:
    out = ai_cost([], "music", "A")
    assert out == {"source": "không có ai_log.jsonl (bản nháp ghép tay, chưa gọi Claude)"}
    assert CostFacts(**out, render_sec=1.0).ai_run_cost_vnd == 0.0


def test_one_round_for_two_drafts_is_split_evenly() -> None:
    out = ai_cost([line(1, ["A", "B"], 3538, 2047, 702.42)], "music", "A")
    assert out["ai_rounds"] == 1 and out["ai_run_input_tokens"] == 3538
    assert out["ai_run_cost_vnd"] == pytest.approx(702.42)
    assert out["ai_share_input_tokens"] == pytest.approx(1769.0)
    assert out["ai_share_output_tokens"] == pytest.approx(1023.5)
    assert out["ai_share_cost_vnd"] == pytest.approx(351.21)


def test_a_retry_for_one_draft_charges_that_draft_the_whole_retry() -> None:
    log = [line(1, ["A", "B"], 1000, 400, 100.0), line(2, ["B"], 1500, 200, 60.0)]
    a, b = ai_cost(log, "music", "A"), ai_cost(log, "music", "B")
    assert a["ai_share_cost_vnd"] == pytest.approx(50.0)
    assert b["ai_share_cost_vnd"] == pytest.approx(110.0)  # 50 from round 1 + all of round 2
    assert a["ai_run_cost_vnd"] == b["ai_run_cost_vnd"] == pytest.approx(160.0)


def test_only_the_latest_run_counts_because_the_log_is_append_only() -> None:
    log = [line(1, ["A"], 9000, 9000, 999.0), line(1, ["A"], 100, 50, 5.0),
           line(2, ["A"], 200, 80, 7.0)]  # fmt: skip
    out = ai_cost(log, "music", "A")
    assert out["ai_rounds"] == 2 and out["ai_run_cost_vnd"] == pytest.approx(12.0)
    assert out["ai_run_input_tokens"] == 300


def test_speech_and_music_runs_do_not_mix() -> None:
    log = [line(1, ["A"], 100, 10, 1.0, "music"), line(1, ["A"], 5000, 900, 50.0, "speech")]
    assert ai_cost(log, "speech", "A")["ai_run_cost_vnd"] == pytest.approx(50.0)
    assert ai_cost(log, "music", "A")["ai_run_cost_vnd"] == pytest.approx(1.0)
    assert last_run(log, "speech") == [log[1]]


def test_an_error_line_without_tokens_costs_nothing_and_does_not_crash() -> None:
    log = [{"mode": "music", "attempt": 1, "drafts": ["A"], "cost_vnd": 0, "error": "timeout"},
           line(2, ["A"], 100, 20, 3.0)]  # fmt: skip
    out = ai_cost(log, "music", "A")
    assert out["ai_run_cost_vnd"] == pytest.approx(3.0) and out["ai_rounds"] == 2


def test_a_draft_the_run_did_not_write_gets_no_share() -> None:
    out = ai_cost([line(1, ["A"], 100, 20, 3.0)], "music", "C")
    assert out["ai_share_cost_vnd"] == 0.0 and out["ai_run_cost_vnd"] == pytest.approx(3.0)


def test_sha256_is_the_standard_hex_digest() -> None:
    assert sha256_of(b"") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
