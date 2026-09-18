from __future__ import annotations

import asyncio
from pathlib import Path

from pypdf import PdfReader

from backend.adapters.base import FetchedContent, SourceAdapter


class PDFAdapter(SourceAdapter):
    source_type = "pdf"

    @classmethod
    def detect(cls, value: str | Path) -> bool:
        return isinstance(value, Path) and value.suffix.lower() == ".pdf"

    async def fetch(self, value: str | Path, **kwargs: object) -> FetchedContent:
        return await asyncio.to_thread(self._fetch_sync, Path(value), kwargs.get("title"))

    def _fetch_sync(self, path: Path, title: object = None) -> FetchedContent:
        reader = PdfReader(path)
        pages = self._native_text(path, reader)
        ocr_pages: list[int] = []
        blank_pages: list[int] = []
        candidates: list[int] = []
        for number, (page, text) in enumerate(zip(reader.pages, pages), 1):
            if not text:
                if self._has_visual_content(page):
                    candidates.append(number)
                else:
                    blank_pages.append(number)
            elif len("".join(text.split())) < 80 and page.images:
                # A page number/header is not evidence that a scanned body was read.
                candidates.append(number)
        page_images: list[str] = []
        if candidates:
            recognized, page_images = self._ocr(path, candidates)
            for number, text in recognized.items():
                if not text:
                    blank_pages.append(number)
                    continue
                original = pages[number - 1]
                pages[number - 1] = (
                    f"{original}\n\n{text}" if original and original != text else text
                )
                ocr_pages.append(number)
        if not any(pages):
            raise ValueError("PDF 未提取到正文，请检查是否为空白文件，或安装并配置扫描件 OCR")
        text = "\n\n".join(
            f"## 第 {index} 页\n\n{page or '（空白页）'}"
            for index, page in enumerate(pages, 1)
        )
        metadata = reader.metadata or {}
        return FetchedContent(
            source_type=self.source_type,
            title=str(title or metadata.get("/Title") or path.stem),
            author=metadata.get("/Author"),
            raw_content=text,
            media_files=[str(path), *page_images],
            metadata={
                "pages": len(reader.pages),
                "ocr_used": bool(ocr_pages),
                "ocr_pages": ocr_pages,
                "blank_pages": sorted(blank_pages),
            },
        )

    @staticmethod
    def _native_text(path: Path, reader: PdfReader) -> list[str]:
        # Predefined CID CMaps (for example UniGB-UTF16-H without ToUnicode)
        # can produce nonempty mojibake in pypdf. MuPDF resolves these mappings;
        # use its real text layer, without routing copyable Chinese through OCR.
        try:
            import pymupdf
        except ImportError as error:
            # Keep the base installation's existing text-only PDF support.
            # Missing CID Unicode maps must not turn this fallback into a
            # successful extraction of undecoded font bytes.
            for number, page in enumerate(reader.pages, 1):
                resource_ref = page.get("/Resources")
                resources = resource_ref.get_object() if resource_ref else {}
                font_ref = resources.get("/Font")
                fonts = font_ref.get_object() if font_ref else {}
                for reference in fonts.values():
                    font = reference.get_object()
                    if font.get("/Subtype") == "/Type0" and "/ToUnicode" not in font:
                        raise RuntimeError(
                            f"PDF 第 {number} 页 CID 文字层需要安装 media 依赖中的 PyMuPDF"
                        ) from error
            return [(page.extract_text() or "").strip() for page in reader.pages]
        with pymupdf.open(path) as document:
            if len(document) != len(reader.pages):
                raise ValueError("PDF 解析器页数不一致，不能确认完整提取")
            return [page.get_text("text").strip() for page in document]

    @staticmethod
    def _has_visual_content(page) -> bool:
        # Empty PDF pages commonly still contain graphics-state/font setup operators.
        # Only painting operators (or annotations) require a rendered-page check.
        if page.get("/Annots"):
            return True
        contents = page.get_contents()
        paint = {b"Do", b"INLINE IMAGE", b"sh", b"S", b"s", b"f", b"F", b"f*",
                 b"B", b"B*", b"b", b"b*", b"Tj", b"TJ", b"'", b'"'}
        return contents is not None and any(
            operator in paint for _, operator in contents.operations
        )

    def _ocr(self, path: Path, page_numbers: list[int]) -> tuple[dict[int, str], list[str]]:
        try:
            import pymupdf
            import pytesseract
            from PIL import Image
        except ImportError as error:
            raise RuntimeError(
                f"PDF 第 {page_numbers[0]} 页 OCR 需要安装 media 依赖"
            ) from error

        image_dir = self.config.data_dir / "derived" / path.stem
        image_dir.mkdir(parents=True, exist_ok=True)
        texts: dict[int, str] = {}
        images: list[str] = []
        with pymupdf.open(path) as document:
            for number in page_numbers:
                page = document[number - 1]
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                image_path = image_dir / f"page-{number}.png"
                pixmap.save(image_path)
                images.append(str(image_path))
                with Image.open(image_path) as image:
                    # Only an exactly white rendered page is safely skipped without OCR.
                    # A noisy scan, diagram, or faint text must not be labelled blank.
                    if image.convert("RGB").getextrema() == ((255, 255),) * 3:
                        texts[number] = ""
                        continue
                    try:
                        text = pytesseract.image_to_string(
                            image, lang="chi_sim+eng", timeout=120
                        ).strip()
                    except (pytesseract.TesseractError, OSError, RuntimeError) as error:
                        raise RuntimeError(
                            f"PDF 第 {number} 页 OCR 失败，请检查 Tesseract、chi_sim/eng 语言包"
                            f"及页面内容：{error}"
                        ) from error
                if not text:
                    raise RuntimeError(f"PDF 第 {number} 页 OCR 未识别到正文，不能确认完整提取")
                texts[number] = text
        return texts, images
