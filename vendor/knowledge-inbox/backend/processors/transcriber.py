from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _load_model(model: str, threads: int, local_only: bool):
    from faster_whisper import WhisperModel

    return WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads,
                        local_files_only=local_only)


class Transcriber:
    _decoding_options = {"vad_filter": True, "beam_size": 5}
    _media_suffixes = {
        ".aac",
        ".aif",
        ".aiff",
        ".amr",
        ".avi",
        ".flac",
        ".m4a",
        ".m4b",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp3",
        ".mp4",
        ".oga",
        ".ogg",
        ".opus",
        ".wav",
        ".webm",
        ".wma",
    }
    _video_suffixes = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}

    @classmethod
    def supports_file(cls, path: Path) -> bool:
        return path.suffix.lower() in cls._media_suffixes

    async def transcribe_first(self, media_files: list[str]) -> str | None:
        path = next(
            (Path(value) for value in media_files if self.supports_file(Path(value))),
            None,
        )
        if not path:
            return None
        return await asyncio.to_thread(self._transcribe, path)

    @classmethod
    def provenance(cls) -> dict:
        """Describe this configured local transcription path, not its accuracy."""
        reference = os.getenv("KNOWLEDGE_WHISPER_MODEL", "").strip() or "small"
        snapshot = Path(reference)
        result = {
            "transcription_review_required": True,
            "transcription_engine": "faster-whisper",
            "transcription_model": reference,
            "transcription_model_reference": reference,
            "transcription_decoding": {**cls._decoding_options, "language": "auto"},
            "transcription_runtime": {"device": "cpu", "compute_type": "int8"},
        }
        # A local alias/path need not identify a revision. Record a revision only
        # when the configured reference is a named Hugging Face snapshot.
        repository = snapshot.parent.parent.name
        if (snapshot.parent.name == "snapshots" and repository.startswith("models--")
                and re.fullmatch(r"[0-9a-f]{40}", snapshot.name)):
            result["transcription_model"] = repository.removeprefix("models--").replace("--", "/")
            result["transcription_model_revision"] = snapshot.name
        return result

    @staticmethod
    def _model():
        snapshot = os.getenv("KNOWLEDGE_WHISPER_MODEL", "").strip()
        if snapshot and not Path(snapshot).is_dir():
            return None
        try:
            threads = int(os.getenv("KNOWLEDGE_WHISPER_THREADS", "2"))
            if threads < 1:
                return None
            return _load_model(snapshot or "small", threads, bool(snapshot))
        except Exception:
            return None

    @classmethod
    def _transcribe(cls, path: Path) -> str | None:
        model = cls._model()
        if model is None:
            return None
        transcript = cls._transcribe_path(model, path)
        if transcript or path.suffix.lower() not in cls._video_suffixes:
            return transcript
        return cls._transcribe_video_audio(model, path)

    @classmethod
    def _transcribe_path(cls, model, path: Path) -> str | None:
        segments, _ = model.transcribe(str(path), **cls._decoding_options)
        transcript = "\n".join(
            segment.text.strip() for segment in segments if segment.text.strip()
        )
        return transcript or None

    @classmethod
    def _transcribe_video_audio(cls, model, path: Path) -> str | None:
        """Retry video transcription through a normalized PCM audio stream.

        Some MP4/AMR combinations expose a valid audio stream to ffmpeg but do
        not yield frames through the PyAV path used by faster-whisper. Keep the
        original file untouched and use a short-lived WAV only for the retry.
        """
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return None
        try:
            with tempfile.TemporaryDirectory(prefix="knowledge-manager-transcribe-") as directory:
                audio = Path(directory) / "audio.wav"
                result = subprocess.run(
                    [
                        ffmpeg,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-nostdin",
                        "-y",
                        "-i",
                        str(path),
                        "-map",
                        "0:a:0",
                        "-vn",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-c:a",
                        "pcm_s16le",
                        str(audio),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=180,
                )
                if result.returncode != 0 or not audio.is_file() or audio.stat().st_size <= 44:
                    return None
                return cls._transcribe_path(model, audio)
        except (OSError, subprocess.SubprocessError):
            return None
