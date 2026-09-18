from __future__ import annotations

import asyncio
import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _load_model(model: str, threads: int, local_only: bool):
    from faster_whisper import WhisperModel

    return WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=threads,
                        local_files_only=local_only)


class Transcriber:
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
        segments, _ = model.transcribe(str(path), vad_filter=True, beam_size=5)
        return "\n".join(segment.text.strip() for segment in segments if segment.text.strip())
