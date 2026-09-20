"""Real Tesseract acceptance on a synthetic PDF mixing text, scan, and blank pages."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

import pymupdf
from backend.adapters.pdf import PDFAdapter
from backend.config import AppConfig

ROOT = Path(__file__).resolve().parents[1]
SCAN_TEXT = '扫描资料验收\n项目预算：12800 元\n负责人：李明\n截止日期：2026-09-30\n行动：保留原件，完成知识归档。'


def create_sample(destination: Path):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document, pymupdf.open() as scan:
        first = document.new_page()
        first.insert_textbox(pymupdf.Rect(40, 40, 550, 760),
                             '可复制文字页：保存原始资料与来源。\n' * 8,
                             fontname='china-s', fontsize=18)
        raster = scan.new_page()
        raster.insert_textbox(pymupdf.Rect(40, 40, 550, 760), SCAN_TEXT,
                              fontname='china-s', fontsize=22, lineheight=1.8)
        second = document.new_page()
        second.insert_image(second.rect, stream=raster.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes('png'))
        document.new_page()
        document.set_metadata({'title': '合成中文混合扫描验收', 'creator': 'Knowledge Manager acceptance'})
        document.save(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample-dir', type=Path, default=ROOT / 'runtime' / 'samples')
    parser.add_argument('--generate-only', action='store_true')
    args = parser.parse_args()
    path = args.sample_dir / 'chinese-mixed-scan.pdf'
    create_sample(path)
    if args.generate_only:
        print(path)
        return 0
    config = AppConfig(data_dir=args.sample_dir / 'ocr-derived')
    content = asyncio.run(PDFAdapter(config).fetch(path))
    compact = ''.join(content.raw_content.split())
    checks = {
        'text_page': '可复制文字页' in compact,
        'amount': '12800' in compact,
        'owner': '李明' in compact,
        'date': '2026-09-30' in compact,
        'archive': '归档' in compact,
        'scanned_page_ocr': content.metadata.get('ocr_pages') == [2],
        'blank_page': content.metadata.get('blank_pages') == [3],
    }
    result = {'file': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'text': content.raw_content, 'metadata': content.metadata,
              'checks': checks, 'passed': all(checks.values())}
    (args.sample_dir / 'chinese-ocr-result.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
