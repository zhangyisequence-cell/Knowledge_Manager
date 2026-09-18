import sys
from types import SimpleNamespace

from backend.processors.transcriber import Transcriber


def test_explicit_speech_snapshot_is_offline_and_reused(tmp_path, monkeypatch):
    calls = []
    model = object()
    monkeypatch.setenv('KNOWLEDGE_WHISPER_MODEL', str(tmp_path))
    monkeypatch.setenv('KNOWLEDGE_WHISPER_THREADS', '1')

    def load(path, **kwargs):
        calls.append((path, kwargs))
        return model

    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=load))
    assert Transcriber._model() is model
    assert Transcriber._model() is model
    assert calls == [(str(tmp_path), {'device': 'cpu', 'compute_type': 'int8',
                                      'cpu_threads': 1, 'local_files_only': True})]


def test_missing_explicit_snapshot_never_attempts_remote_download(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv('KNOWLEDGE_WHISPER_MODEL', str(tmp_path / 'missing'))
    monkeypatch.setitem(sys.modules, 'faster_whisper',
                        SimpleNamespace(WhisperModel=lambda *a, **k: calls.append(a)))
    assert Transcriber._model() is None
    assert calls == []
