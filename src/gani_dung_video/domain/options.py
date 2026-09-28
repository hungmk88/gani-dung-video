"""Option block chosen by the user (spec v2.1 Appendix D) + footage facts known at preflight.

Why strict types: values arrive from a hand-written yeu-cau.md or the future web UI; a
duration of "30" or a rights tick of "true" must be rejected, not silently coerced.
Kiểu dữ liệu chặt: "30" hay "true" dạng chữ bị từ chối, không tự đổi kiểu.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

PositiveNumber = Annotated[float, Field(gt=0, strict=True)]
Count = Annotated[int, Field(ge=0, strict=True)]


class KhuyenMai(BaseModel):
    """Promotion details — required (non-blank) when muc_dich = khuyen_mai (X3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    uu_dai: StrictStr = ""
    thoi_han: StrictStr = ""
    dieu_kien: StrictStr = ""


class VideoOptions(BaseModel):
    """One option combo. Choice values are checked against rules.json, not hardcoded here.

    Footage facts (so_canh_dung_duoc, co_loi_noi, nhac_bpm, nhac_giay) are filled by the
    inventory/recognition steps; None means "not known yet" and the related check is skipped.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    phong_cach: StrictStr
    muc_dich: StrictStr
    do_dai_giay: StrictInt
    so_ban_nhap: StrictInt
    nhac: StrictStr
    nhac_file: StrictStr | None = None
    nhac_quyen_su_dung: StrictBool | None = None
    khuyen_mai: KhuyenMai | None = None
    ghi_chu: StrictStr | None = None
    # Appendix D writes `rules_version: 2.0` unquoted, so accept the number form too.
    rules_version: StrictStr | float | None = None
    so_canh_dung_duoc: Count | None = None
    co_loi_noi: StrictBool | None = None
    nhac_bpm: PositiveNumber | None = None
    nhac_giay: PositiveNumber | None = None
