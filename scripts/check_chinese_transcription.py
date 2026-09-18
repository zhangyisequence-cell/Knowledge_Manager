"""Explicit real-model acceptance using synthetic Chinese WAV/video/WeChat AMR."""
import argparse
import hashlib
import json
import time
from pathlib import Path

from faster_whisper import WhisperModel
from huggingface_hub import hf_hub_download

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true', help='Allow downloading the public model')
    parser.add_argument('--revision', default='536b0662742c02347bc0e980a01041f333bce120',
                        help='Pinned Systran/faster-whisper-small snapshot')
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--sample-dir', type=Path, default=ROOT / 'runtime' / 'samples')
    parser.add_argument('--cache-dir', type=Path, default=ROOT / 'runtime' / 'model-cache' / 'hub')
    args = parser.parse_args()
    start = time.monotonic()
    # These are the four published files required by this fixed CTranslate2 model.
    # Fetching each exact file also works with mirrors lacking Hub tree pagination.
    files = [hf_hub_download('Systran/faster-whisper-small', filename, revision=args.revision,
                             cache_dir=str(args.cache_dir), local_files_only=not args.download,
                             token=False)
             for filename in ('config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt')]
    snapshot = Path(files[0]).parent
    if any(Path(path).parent != snapshot for path in files):
        raise RuntimeError('Model files do not belong to one snapshot')
    model = WhisperModel(str(snapshot), device='cpu', compute_type='int8',
                         cpu_threads=args.threads, local_files_only=True)
    results = []
    for suffix in ('wav', 'mp4', 'amr'):
        path = args.sample_dir / f'chinese-validation.{suffix}'
        segments, info = model.transcribe(str(path), language='zh', vad_filter=True, beam_size=5)
        text = ''.join(segment.text.strip() for segment in segments)
        # No initial prompt or expected-answer hint is supplied to the model.
        checks = {
            'amount_12800': any(amount in text for amount in ('12800', '12,800', '一万二千八百', '一万两千八百')),
            'owner': '李明' in text,
            'weekly_review': '周五' in text and '复盘' in text,
            'deadline': '下周三' in text,
            'archive_action': '归档' in text,
        }
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
