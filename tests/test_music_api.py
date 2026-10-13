"""Tests: GET /api/music and GET /media/music/<file> on a REAL server (ticket 14/10/2026).

The list must never carry an absolute path, the audio route must refuse everything that is not
a plain audio file name inside the music folder, and the same Host / Origin rules as the other
routes apply.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from gani_dung_video.application.api_service import ApiContext
from gani_dung_video.infrastructure.config_files import load_rules
from gani_dung_video.infrastructure.http_server import make_server
from gani_dung_video.infrastructure.queue_db import QueueDb
from gani_dung_video.infrastructure.settings import Settings

RULES = load_rules()
VITE = "http://localhost:5173"


@pytest.fixture(autouse=True)
def _trust_the_dev_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing but the server itself is trusted by default; these tests act as the Vite UI."""
    monkeypatch.setenv("GANI_DV_DEV_ORIGINS", VITE)


SR = 8000


def click_track(bpm: float, dur: float = 20.0, first: float = 0.5) -> np.ndarray:
    period = 60.0 / bpm
    x = np.zeros(int(dur * SR))
    t = np.arange(int(0.3 * SR)) / SR
    burst = np.sin(2 * np.pi * (55 + 90 * np.exp(-25 * t)) * t) * np.exp(-9 * t)
    k, at = 0, first
    while at < dur - 0.3:
        i = int(at * SR)
        x[i : i + len(burst)] += burst * (1.0 if k % 4 == 0 else 0.55)
        k, at = k + 1, first + (k + 1) * period
    x += 0.01 * np.random.default_rng(1).normal(size=len(x))
    return x


def write_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


class Api:
    def __init__(self, port: int, music: Path, queue: Path) -> None:
        self.port, self.music, self.queue = port, music, queue

    def call(
        self, method: str, path: str, headers: dict[str, str] | None = None,
        host: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:  # fmt: skip
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.putrequest(method, path, skip_host=host is not None)
            if host is not None:
                conn.putheader("Host", host)
            for k, v in (headers or {}).items():
                conn.putheader(k, v)
            conn.endheaders()
            r = conn.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            conn.close()

    def music_json(self) -> dict[str, Any]:
        status, _h, body = self.call("GET", "/api/music")
        assert status == 200, body
        data: dict[str, Any] = json.loads(body)
        return data

    def wait_measured(self, timeout: float = 60.0) -> dict[str, Any]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            data = self.music_json()
            if data["pending"] == 0:
                return data
            time.sleep(0.2)
        raise AssertionError("the background measurement did not finish")


def serve(tmp_path: Path, music: Path | None, **extra: str) -> Iterator[Api]:
    rules = {**RULES, "api": {**RULES["api"], "frontend_dist": None}}
    queue = tmp_path / "q"
    settings = Settings(gani_dv_queue_dir=str(queue),
                        gani_dv_music_dir=str(music) if music is not None else None,
                        **extra)  # fmt: skip
    ctx = ApiContext(QueueDb(queue / "queue.db"), rules, settings)
    server = make_server(ctx, port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        yield Api(int(server.server_address[1]), music or tmp_path, queue)
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=10)


@pytest.fixture
def api(tmp_path: Path) -> Iterator[Api]:
    music = tmp_path / "nhac"
    music.mkdir()
    write_wav(music / "lib.wav", click_track(90))
    write_wav(music / "t100.wav", click_track(100))
    (music / "library.json").write_text(
        json.dumps({"tracks": {"lib.wav": {"bpm": 90, "first_beat_sec": 0.4, "title": "Ấm áp",
                                           "rights": "royalty_free"}}}),
        encoding="utf-8",
    )  # fmt: skip
    (tmp_path / "secret.wav").write_bytes(b"RIFFsecret")  # next to the folder, not in it
    yield from serve(tmp_path, music)


@pytest.fixture
def unconfigured(tmp_path: Path) -> Iterator[Api]:
    yield from serve(tmp_path, None)


# ─────────────── GET /api/music ───────────────


def test_no_music_folder_configured_is_an_empty_list_not_an_error(unconfigured: Api) -> None:
    assert unconfigured.music_json() == {"configured": False, "pending": 0, "tracks": []}


def test_a_folder_that_does_not_exist_is_the_same_empty_answer(tmp_path: Path) -> None:
    for a in serve(tmp_path, tmp_path / "khong_co"):
        assert a.music_json()["configured"] is False


def test_the_list_has_library_and_measured_tracks_and_a_pending_count(api: Api) -> None:
    data = api.wait_measured()
    by = {t["file"]: t for t in data["tracks"]}
    assert data["configured"] is True and set(by) == {"lib.wav", "t100.wav"}
    lib, measured = by["lib.wav"], by["t100.wav"]
    assert (lib["bpm"], lib["bpm_source"], lib["title"], lib["rights"]) == (
        90.0, "library", "Ấm áp", "royalty_free")  # fmt: skip
    assert measured["bpm_source"] == "measured" and measured["bpm"] == pytest.approx(100.0, abs=0.6)
    assert measured["title"] == "t100" and measured["rights"] == "thu_vien_noi_bo"
    assert lib["duration_sec"] == pytest.approx(20.0, abs=0.2)
    assert lib["path"] == "lib.wav"  # relative to the folder (it is not walked)


def test_no_absolute_path_leaves_the_server(api: Api, tmp_path: Path) -> None:
    api.wait_measured()
    status, _h, body = api.call("GET", "/api/music")
    text = body.decode("utf-8")
    assert status == 200 and str(tmp_path) not in text and str(api.music) not in text
    assert "\\\\" not in text and ":/" not in text and "/tmp" not in text


def test_the_cache_sits_next_to_queue_db(api: Api) -> None:
    api.wait_measured()
    assert (api.queue / "music-cache.json").is_file()


def test_a_broken_library_json_is_a_clear_error_and_the_server_stays_up(api: Api) -> None:
    (api.music / "library.json").write_text("{khong phai json", encoding="utf-8")
    status, _h, body = api.call("GET", "/api/music")
    err = json.loads(body)["error"]
    assert (
        status == 422
        and err["code"] == "music_library_invalid"
        and "library.json" in err["message"]
    )
    assert api.call("GET", "/api/health")[0] == 200


def test_music_list_needs_a_trusted_host_and_origin(api: Api) -> None:
    assert api.call("GET", "/api/music", host="evil.com")[0] == 403
    assert api.call("GET", "/api/music", headers={"Origin": "http://evil.com"})[0] == 403
    assert api.call("GET", "/api/music", headers={"Origin": VITE})[0] == 200


def test_only_get_is_allowed(api: Api) -> None:
    assert api.call("POST", "/api/music")[0] in (405, 415)


# ─────────────── GET /media/music/<file> ───────────────


def test_a_track_is_served_with_its_type_and_ranges(api: Api) -> None:
    size = (api.music / "lib.wav").stat().st_size
    status, h, body = api.call("GET", "/media/music/lib.wav")
    assert status == 200 and h["content-type"] == "audio/wav" and len(body) == size
    assert h["accept-ranges"] == "bytes"
    status, h, part = api.call("GET", "/media/music/lib.wav", headers={"Range": "bytes=10-19"})
    assert status == 206 and part == body[10:20]
    assert h["content-range"] == f"bytes 10-19/{size}"
    assert api.call("GET", "/media/music/lib.wav", headers={"Range": f"bytes={size}-"})[0] == 416
    status, h, empty = api.call("HEAD", "/media/music/lib.wav")
    assert status == 200 and empty == b"" and h["content-length"] == str(size)


@pytest.mark.parametrize(
    "path",
    [
        "/media/music/..%2Fsecret.wav", "/media/music/%2e%2e%2fsecret.wav",
        "/media/music/..%5Csecret.wav", "/media/music/a%5Cb.wav", "/media/music/%2Fetc%2Fpasswd",
        "/media/music/C%3A%5CWindows%5Cwin.ini", "/media/music/.an.wav",
        "/media/music/library.json",
        "/media/music/khong_co.wav", "/media/music/notes.txt", "/media/music/x.exe",
        "/media/music/lib.wav%00.txt", "/media/music/%20lib.wav", "/media/music/lib.WAV%20",
    ],
)  # fmt: skip
def test_everything_but_a_plain_audio_file_in_the_folder_is_a_404(api: Api, path: str) -> None:
    status, _h, body = api.call("GET", path)
    assert status == 404, (path, status)
    assert b"RIFFsecret" not in body


def test_a_link_that_leaves_the_folder_is_refused(api: Api, tmp_path: Path) -> None:
    link = api.music / "ra_ngoai.wav"
    try:
        link.symlink_to(tmp_path / "secret.wav")
    except OSError:
        pytest.skip("không tạo được liên kết tượng trưng trên máy này")
    assert api.call("GET", "/media/music/ra_ngoai.wav")[0] == 404
    assert "ra_ngoai.wav" not in {t["file"] for t in api.music_json()["tracks"]}


def test_a_name_with_spaces_and_accents_works(api: Api) -> None:
    write_wav(api.music / "Nhạc nền 01.wav", click_track(95))
    status, _h, body = api.call("GET", "/media/music/Nh%E1%BA%A1c%20n%E1%BB%81n%2001.wav")
    assert status == 200 and body[:4] == b"RIFF"


def test_audio_needs_a_trusted_host_and_origin_too(api: Api) -> None:
    assert api.call("GET", "/media/music/lib.wav", host="evil.com")[0] == 403
    site = {"Sec-Fetch-Site": "cross-site"}
    assert api.call("GET", "/media/music/lib.wav", headers=site)[0] == 403
    assert (
        api.call("GET", "/media/music/lib.wav", headers={**site, "Referer": VITE + "/"})[0] == 200
    )


def test_audio_without_a_configured_folder_is_a_404(unconfigured: Api) -> None:
    assert unconfigured.call("GET", "/media/music/lib.wav")[0] == 404
