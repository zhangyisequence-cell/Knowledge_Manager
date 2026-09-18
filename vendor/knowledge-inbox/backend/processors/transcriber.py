from __future__ import annotations

import asyncio
from pathlib import Path


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
        try:
            from faster_whisper import WhisperModel
        except Exception:
            return None
        try:
            return WhisperModel("small", device="auto", compute_type="int8")
        except Exception:
            return None

    @classmethod
    def _transcribe(cls, path: Path) -> str | None:
        model = cls._model()
        if model is None:
            return None
        segments, _ = model.transcribe(str(path), vad_filter=True, beam_size=5)
        return "\n".join(segment.text.strip() for segment in segments if segment.text.strip())
