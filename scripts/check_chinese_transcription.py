"""Explicit real-model acceptance using synthetic Chinese WAV/video/WeChat AMR."""
import argparse
import hashlib
import json
import time
from pathlib import Path

from faster_whisper import WhisperModel
from hf_snapshot_download import PINNED_REVISION, ensure_snapshot

ROOT = Path(__file__).resolve().parents[1]


def evaluate_checks(text):
    return {
        'amount_12800': any(amount in text for amount in
                            ('12800', '12,800', '一万二千八百', '一万两千八百')),
        'owner': '李明' in text,
        'weekly_review': any(day in text for day in ('周五', '週五'))
                         and any(review in text for review in ('复盘', '復盤')),
        'deadline': any(deadline in text for deadline in ('下周三', '下週三')),
        'archive_action': any(action in text for action in ('归档', '歸檔')),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true', help='Allow downloading the public model')
    parser.add_argument('--revision', default=PINNED_REVISION,
                        help='Pinned Systran/faster-whisper-small snapshot')
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--sample-dir', type=Path, default=ROOT / 'runtime' / 'samples')
    parser.add_argument('--cache-dir', type=Path, default=ROOT / 'runtime' / 'model-cache' / 'hub')
    args = parser.parse_args()
    start = time.monotonic()
    snapshot = ensure_snapshot(args.cache_dir, args.revision, allow_download=args.download)
    model = WhisperModel(str(snapshot), device='cpu', compute_type='int8',
                         cpu_threads=args.threads, local_files_only=True)
    results = []
    for suffix in ('wav', 'mp4', 'amr'):
        path = args.sample_dir / f'chinese-validation.{suffix}'
        segments, info = model.transcribe(str(path), language='zh', vad_filter=True, beam_size=5)
        text = ''.join(segment.text.strip() for segment in segments)
        # No initial prompt or expected-answer hint is supplied to the model.
        checks = evaluate_checks(text)
        results.append({'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'duration': info.duration, 'transcript': text, 'checks': checks})
    result = {'model': 'Systran/faster-whisper-small', 'revision': args.revision,
              'device': 'cpu', 'compute_type': 'int8',
              'elapsed_seconds': round(time.monotonic() - start, 2), 'samples': results,
              'passed': all(all(row['checks'].values()) for row in results)}
    destination = args.sample_dir / 'chinese-transcription-result.json'
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
