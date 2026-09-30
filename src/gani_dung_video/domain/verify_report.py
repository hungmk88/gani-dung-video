"""What the checks said, round by round (phase 6i) — the record behind edit/verify_report.json.

Until 6i the V1–V9 (music) / S1–S6 (speech) findings of a draft lived only in memory: a draft
that failed wrote nothing, so nobody could say afterwards *why* (the real 30s run that failed three
rounds on V4 cost 2.992đ and left no trace). One `RoundRecord` per Claude call keeps the findings;
`check_rows` turns a round into one row per check for the UI. Pure: no file or network access.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel

MAX_FINDINGS_PER_ROUND = 50  # a runaway answer must not bloat the file
MAX_MSG_CHARS = 300
MAX_DETAIL_MSGS = 3

MUSIC_CODES = ("V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9", "SNAP")
SPEECH_CODES = ("S1", "S2", "S3", "S4", "S5", "S6", "SNAP")

Mode = Literal["music", "speech"]
Level = Literal["ai", "auto", "warn"]
FinalStatus = Literal["running", "passed", "failed", "cost_cap", "api_error"]
CheckStatus = Literal["pass", "fail", "fixed", "warn", "skipped"]

_CODES: dict[Mode, tuple[str, ...]] = {"music": MUSIC_CODES, "speech": SPEECH_CODES}
_LEVEL_STATUS: dict[str, CheckStatus] = {"ai": "fail", "auto": "fixed", "warn": "warn"}
_ORDER: tuple[CheckStatus, ...] = ("fail", "fixed", "warn", "pass")  # strongest first


class IssueLike(Protocol):
    """verify.Issue / speech_verify.Issue: the four fields this module reads."""

    @property
    def code(self) -> str: ...
    @property
    def level(self) -> str: ...
    @property
    def msg(self) -> str: ...
    @property
    def draft(self) -> str | None: ...


class Finding(BaseModel):
    code: str
    level: Level
    draft: str | None
    msg: str


class RoundRecord(BaseModel):
    round: int
    drafts: list[str]  # the drafts Claude was asked for in this round
    parsed: bool  # False: the answer could not be read, so the checks never ran on it
    passed: bool  # no finding the AI must fix
    cost_vnd: float
    input_tokens: int
    output_tokens: int
    stop_reason: str
    findings: list[Finding]


class VerifyReportFile(BaseModel):
    """Content of edit/verify_report.json."""

    version: int = 1
    mode: Mode
    final_status: FinalStatus
    rounds: list[RoundRecord]
    # Checks that ran on top of the mode's list (V10 for a 3-minute plan), shown before SNAP.
    extra_codes: list[str] = []


@dataclass(frozen=True)
class CheckRow:
    code: str
    status: CheckStatus
    messages: tuple[str, ...]


def _cut(msg: str) -> str:
    return msg if len(msg) <= MAX_MSG_CHARS else msg[: MAX_MSG_CHARS - 1] + "…"


def _finding(i: IssueLike) -> Finding:
    level: Level = i.level if i.level in _LEVEL_STATUS else "warn"  # type: ignore[assignment]
    return Finding(code=i.code, level=level, draft=i.draft, msg=_cut(i.msg))


def make_round(
    number: int,
    drafts: Sequence[str],
    parsed: bool,
    cost_vnd: float,
    input_tokens: int,
    output_tokens: int,
    stop_reason: str,
    issues: Sequence[IssueLike],
) -> RoundRecord:
    """One round from what the loop knows right after the checks ran."""
    findings = [_finding(i) for i in issues]
    return RoundRecord(
        round=number,
        drafts=list(drafts),
        parsed=parsed,
        passed=not any(i.level == "ai" for i in issues),  # judged on all issues, not the kept 50
        cost_vnd=cost_vnd,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stop_reason=stop_reason,
        findings=findings[:MAX_FINDINGS_PER_ROUND],
    )


def _row(code: str, findings: Sequence[Finding]) -> CheckRow:
    mine = [f for f in findings if f.code == code]
    statuses = {_LEVEL_STATUS[f.level] for f in mine}
    status: CheckStatus = next((s for s in _ORDER if s in statuses), "pass")
    return CheckRow(code, status, tuple(f.msg for f in mine))


def _codes_of(mode: Mode, extra: Sequence[str]) -> list[str]:
    """The mode's codes with `extra` ones placed just before SNAP (the last check)."""
    base = list(_CODES[mode])
    new = [c for c in extra if c not in base]
    return [*base[:-1], *new, base[-1]] if new else base


def check_rows(mode: Mode, record: RoundRecord, extra: Sequence[str] = ()) -> list[CheckRow]:
    """One row per check of `mode`, in order; a code nobody listed is appended at the end.

    An unreadable answer never reached the checks: the first one takes the blame (it reports
    the broken structure) and the rest are "skipped" rather than falsely "pass".
    """
    codes = _codes_of(mode, extra)
    codes += sorted({f.code for f in record.findings} - set(codes))
    rows = [_row(c, record.findings) for c in codes]
    if record.parsed:
        return rows
    listed = set(_codes_of(mode, extra))
    return [r if n == 0 or r.code not in listed else CheckRow(r.code, "skipped", r.messages)
            for n, r in enumerate(rows)]  # fmt: skip


def check_detail(row: CheckRow) -> str:
    """The sentence shown next to a row: empty when clean, else the distinct messages."""
    unique = list(dict.fromkeys(row.messages))
    shown = "; ".join(unique[:MAX_DETAIL_MSGS])
    rest = len(unique) - MAX_DETAIL_MSGS
    return f"{shown} (+{rest} nữa)" if rest > 0 else shown
