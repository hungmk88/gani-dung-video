"""Write a text file so that a reader (or a crash) never sees half of it."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path


def write_text_atomic(path: Path, text: str) -> None:
    """Write `text` to a temp file beside `path`, then `os.replace` it over `path`.

    The replace is atomic on Windows and POSIX when both names are on one volume, which they
    are: the temp file lives in the same folder. On any error the old file is left as it was
    and the temp file is removed.
    """
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
