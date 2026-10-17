"""Platform profiles (14/10/2026): one draft rendered once per platform it is posted on.

TikTok and Facebook Reels get the same footage, script and cuts; a profile in
rules.json → platforms changes only what the platform's moderation is strict about:

- the call-to-action caption (`cta`, per purpose; a purpose not listed keeps the plan's text),
- the Zalo line of the end card (`zalo_on_end_card`),
- the folder the render is written to and the delivered file name.

The empty platform "" means "no profile": the behaviour from before, kept for old queue rows and
for the command line without `--nen-tang`. Pure code.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gani_dung_video.domain.edl import Caption, Edl
from gani_dung_video.domain.preflight import Rules

__all__ = ["PlatformError", "apply_platform", "platform_ids", "profile", "render_dir_name"]


class PlatformError(ValueError):
    """An unknown platform id, or a profile in rules.json that cannot be used (Vietnamese)."""


def platform_ids(rules: Rules) -> list[str]:
    """Platform ids in rules.json order (e.g. ["tiktok", "facebook"])."""
    return [k for k in rules["platforms"]["profiles"]]


def profile(rules: Rules, platform: str) -> Mapping[str, Any] | None:
    """The profile of `platform`, None for "" (no profile).

    Raises:
        PlatformError: `platform` is not in rules.json → platforms.profiles.
    """
    if not platform:
        return None
    profiles = rules["platforms"]["profiles"]
    if platform not in profiles:
        raise PlatformError(f"Nền tảng '{platform}' không có trong rules.json → platforms "
                            f"(có: {', '.join(profiles)}).")  # fmt: skip
    p: Mapping[str, Any] = profiles[platform]
    return p


def render_dir_name(letter: str, platform: str) -> str:
    """Folder of one render under edit/render/: the draft letter, plus the platform when there
    is one ("A", "A-tiktok"), so the TikTok and the Facebook render never overwrite each other."""
    return f"{letter}-{platform}" if platform else letter


def _cta_text(p: Mapping[str, Any], purpose: str) -> str | None:
    text = p.get("cta", {}).get(purpose)
    if text is None:
        return None
    if "[" in str(text):  # "[ngày]" is filled from the options at plan time, not available here
        raise PlatformError(f"CTA của nền tảng cho mục đích '{purpose}' không được chứa ô "
                            f"thay thế như [ngày]: '{text}'.")  # fmt: skip
    return str(text)


def apply_platform(e: Edl, rules: Rules, purpose: str, platform: str) -> Edl:
    """The EDL as posted on `platform`: CTA caption and end card per its profile.

    The CTA block may carry several caption lines written by the AI; with a profile CTA they
    become one line spanning the same time, so no stray word of the old call stays on screen.

    Raises:
        PlatformError: unknown platform or unusable profile.
    """
    p = profile(rules, platform)
    if p is None:
        return e
    update: dict[str, Any] = {"platform": platform,
                              "end_card_zalo": bool(p.get("zalo_on_end_card", True))}  # fmt: skip
    cta = _cta_text(p, purpose)
    old = [c for c in e.captions if c.kind == "cta"]
    if cta is not None and old:
        merged = Caption(start=min(c.start for c in old), end=max(c.end for c in old), text=cta,
                         kind="cta")  # fmt: skip
        first = e.captions.index(old[0])
        rest = [c for c in e.captions if c.kind != "cta"]
        update["captions"] = [*rest[:first], merged, *rest[first:]]
    return e.model_copy(update=update)
