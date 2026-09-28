"""18 conflict scenarios, each a hand-written yeu-cau.md (spec Appendix D), run end to end.

Covers the whole path the pipeline will use: read file → parse block → validate → preflight.
X8, X16, X17, X18 belong to later stages (plan / AI / render): their fixtures must PASS
preflight untouched, proving preflight does not guess at things it cannot know.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gani_dung_video.application.preflight_service import check_options_file
from gani_dung_video.domain.preflight import Result
from gani_dung_video.infrastructure.config_files import load_rules

FIXTURES = Path(__file__).parent / "fixtures" / "yeu-cau"
RULES = load_rules()

# file → (valid, exact set of issue codes)
EXPECTED: dict[str, tuple[bool, set[str]]] = {
    "00-hop-le-phu-luc-d.md": (True, set()),
    "X01-tvc-3-phut.md": (False, {"X1", "W_STYLE_PURPOSE"}),
    "X02-khuyen-mai-3-phut.md": (False, {"X2"}),
    "X03-khuyen-mai-thieu-uu-dai.md": (False, {"X3"}),
    "X04-theo-mo-ta-ghi-chu-ngan.md": (False, {"X4", "W_STYLE_PURPOSE"}),
    "X05-thieu-canh.md": (False, {"X5"}),
    "X06-canh-sat-nut.md": (True, {"X6"}),
    "X07-ban-hang-co-loi-khong-loi.md": (True, {"X7"}),
    "X08-thieu-canh-bat-buoc.md": (True, set()),
    "X09-review-gioi-thieu-xuong.md": (True, {"X9"}),
    "X10-sang-trong-15s.md": (True, {"X10"}),
    "X11-tvc-khong-nhac.md": (True, {"X11"}),
    "X12-bpm-chia-doi.md": (True, {"X12"}),
    "X12-bpm-lech-xa.md": (True, {"X12"}),
    "X13-nhac-ngan.md": (True, {"X13"}),
    "X14-chua-tick-quyen.md": (False, {"X14"}),
    "X15-3-ban-3-phut.md": (True, {"X15", "W_STYLE_PURPOSE"}),
    "X16-ghi-chu-mau-thuan.md": (True, set()),
    "X17-loi-noi-va-nhac.md": (True, set()),
    "X18-phach-va-dinh-dong-tac.md": (True, set()),
    "loi-dinh-dang.md": (False, {"INVALID_FORMAT"}),
}


def _codes(r: Result) -> set[str]:
    return {i["code"] for i in r["errors"] + r["warnings"] + r["adjustments"]}


def test_every_fixture_has_an_expectation() -> None:
    assert {p.name for p in FIXTURES.glob("*.md")} == set(EXPECTED)


def test_all_18_conflicts_have_a_fixture() -> None:
    covered = {name.split("-")[0] for name in EXPECTED if name.startswith("X")}
    assert covered == {f"X{i:02d}" for i in range(1, 19)}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_fixture(name: str) -> None:
    valid, expected = EXPECTED[name]
    r = check_options_file(FIXTURES / name, RULES)
    assert r["valid"] is valid, r
    assert _codes(r) == expected, r
    if not valid:
        assert r["error_msg"], "a blocked combo must tell the user why"


def test_later_stage_conflicts_are_declared_as_such() -> None:
    stages = {k: v["stage"] for k, v in RULES["conflicts"].items()}
    assert {k for k, s in stages.items() if s != "preflight"} == {"X8", "X16", "X17", "X18"}


def test_bpm_messages_describe_the_adjustment() -> None:
    halved = check_options_file(FIXTURES / "X12-bpm-chia-doi.md", RULES)
    assert halved["adjustments"][0]["level"] == "auto" and "64" in halved["adjustments"][0]["msg"]
    far = check_options_file(FIXTURES / "X12-bpm-lech-xa.md", RULES)
    assert far["warnings"][0]["level"] == "warn" and "170" in far["warnings"][0]["msg"]


def test_x3_message_names_only_the_missing_field() -> None:
    r = check_options_file(FIXTURES / "X03-khuyen-mai-thieu-uu-dai.md", RULES)
    assert "thoi_han" in r["error_msg"] and "uu_dai" not in r["error_msg"]
