"""faster-whisper adapter: one clip in, RawTranscript out (Vietnamese, word timestamps, VAD).

The model is loaded once per run (loading `large-v3-turbo` takes seconds and ~1.6 GB RAM).
Models live in the Hugging Face cache (~/.cache/huggingface); a new machine downloads the
model on first use, after that it runs offline.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from gani_dung_video.domain.speech import RawTranscript, SpokenWord

Transcriber = Callable[[Path], RawTranscript]
# ctranslate2 raises RuntimeError/ValueError; Hugging Face download and PyAV decode errors
# are OSError subclasses.
_BACKEND_ERRORS = (RuntimeError, ValueError, OSError)


class AsrError(RuntimeError):
    """The recogniser could not be loaded or could not read a clip (message in Vietnamese)."""


def load_transcriber(model: str, cfg: Mapping[str, Any]) -> Transcriber:
    """Load faster-whisper once and return a function that transcribes one clip.

    Raises:
        AsrError: faster-whisper missing or the model cannot be loaded/downloaded.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise AsrError("Chưa cài faster-whisper: chạy `pip install faster-whisper`.") from e
    # Device comes from config, never "auto": auto picked an unsupported laptop GPU and crashed.
    hw = {"device": str(cfg["device"]), "compute_type": str(cfg["compute_type"])}
    try:
        # Offline first: a cached model must not wait on a flaky network to check for updates.
        wm = WhisperModel(model, **hw, local_files_only=True)
    except _BACKEND_ERRORS:
        try:
            wm = WhisperModel(model, **hw)  # download once
        except _BACKEND_ERRORS as e:
            raise AsrError(
                f"Không nạp được model Whisper '{model}': {e}. "
                "Máy mới cần mạng để tải model lần đầu."
            ) from e

    def transcribe(path: Path) -> RawTranscript:
        try:
            segments, info = wm.transcribe(
                str(path), language=cfg["language"], vad_filter=bool(cfg["vad_filter"]),
                word_timestamps=True, beam_size=int(cfg["beam_size"]),
            )  # fmt: skip
            words = [
                SpokenWord(text=w.word, start=round(w.start, 3), end=round(w.end, 3),
                           prob=round(w.probability, 3))
                for seg in segments for w in (seg.words or [])
            ]  # fmt: skip
        except _BACKEND_ERRORS as e:
            raise AsrError(f"Không nhận dạng được {path.name}: {e}") from e
        speech = info.duration_after_vad if info.duration_after_vad is not None else info.duration
        return RawTranscript(language=info.language, duration_sec=info.duration,
                             speech_sec=speech, words=words)  # fmt: skip

    return transcribe
