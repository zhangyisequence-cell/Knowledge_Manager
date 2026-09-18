"""Real PDF extraction/rendering; only the external OCR engine is substituted."""

import builtins
import io
from pathlib import Path

import pymupdf
import pytesseract
import pytest
from backend.adapters.pdf import PDFAdapter
from backend.config import AppConfig
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


def make_pdf(path, pages):
    document = canvas.Canvas(str(path), pagesize=(600, 800))
    for kind, text in pages:
        if kind == "text":
            document.drawString(40, 740, text)
        elif kind in {"scan", "white_scan"}:
            image = Image.new("RGB", (500, 600), "white")
            if kind == "scan":
                ImageDraw.Draw(image).text((30, 30), "Scanned budget: 12800", fill="black")
            content = io.BytesIO()
            image.save(content, format="PNG")
            document.drawImage(ImageReader(content), 40, 100, width=500, height=600)
            if text:
                document.drawString(40, 760, text)
        document.showPage()
    document.save()
    return path


def adapter(tmp_path):
    return PDFAdapter(AppConfig(data_dir=tmp_path / "data"))


LONG_TEXT = "Native text and source must survive. " * 8


def make_chinese_pdf(path):
    expected = "可复制文字页：保存原始资料与来源。"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(40, 40, 550, 760), expected + "\n" + expected,
                            fontname="china-s", fontsize=18)
        document.new_page()
        document.save(path)
    return expected


def test_chinese_predefined_cid_font_preserves_real_text_layer(tmp_path):
    path = tmp_path / "chinese-native.pdf"
    expected = make_chinese_pdf(path)

    result = adapter(tmp_path)._fetch_sync(path)

    assert result.raw_content.count(expected) == 2
    assert result.metadata["ocr_pages"] == []
    assert result.metadata["blank_pages"] == [2]
    assert result.media_files == [str(path)]


def test_missing_cid_decoder_fails_instead_of_saving_garbled_chinese(tmp_path, monkeypatch):
    path = tmp_path / "chinese-native.pdf"
    make_chinese_pdf(path)
    real_import = builtins.__import__

    def unavailable_import(name, *args, **kwargs):
        if name == "pymupdf":
            raise ImportError("optional dependency unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable_import)
    with pytest.raises(RuntimeError, match="第 1 页 CID.*PyMuPDF"):
        adapter(tmp_path)._fetch_sync(path)


def test_base_text_pdf_still_works_without_optional_cid_decoder(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "latin.pdf", [("text", "Native source."), ("blank", "")])
    real_import = builtins.__import__

    def unavailable_import(name, *args, **kwargs):
        if name == "pymupdf":
            raise ImportError("optional dependency unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable_import)
    result = adapter(tmp_path)._fetch_sync(path)
    assert "Native source." in result.raw_content
    assert result.metadata["blank_pages"] == [2]


def test_mixed_pdf_preserves_every_page_in_order(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "mixed.pdf", [
        ("text", LONG_TEXT), ("scan", ""), ("text", "Final source text."), ("scan", ""),
    ])
    recognized = iter(["中文扫描预算：12800 元。", "下一次复盘定在周五。"])
    monkeypatch.setattr(pytesseract, "image_to_string", lambda *a, **k: next(recognized))

    result = adapter(tmp_path)._fetch_sync(path)

    ordered = ["## 第 1 页", "Native text", "## 第 2 页", "中文扫描预算：12800 元。",
               "## 第 3 页", "Final source text.", "## 第 4 页", "下一次复盘定在周五。"]
    positions = [result.raw_content.index(value) for value in ordered]
    assert positions == sorted(positions)
    assert result.metadata["ocr_used"] is True
    assert result.metadata["ocr_pages"] == [2, 4]
    assert result.media_files[0] == str(path)
    assert len(result.media_files) == 3
    assert all(Path(value).is_file() for value in result.media_files)


@pytest.mark.parametrize("failure", [
    "empty", "missing_executable", "missing_language", "missing_dependency", "timeout",
])
def test_unreadable_scan_never_succeeds_with_only_other_pages(tmp_path, monkeypatch, failure):
    path = make_pdf(tmp_path / "unreadable.pdf", [("text", LONG_TEXT), ("scan", "")])
    if failure == "missing_executable":
        monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", str(tmp_path / "no-tesseract"))
    elif failure == "missing_language":
        def unavailable(*args, **kwargs):
            raise pytesseract.TesseractError(1, "Error opening data file chi_sim.traineddata")
        monkeypatch.setattr(pytesseract, "image_to_string", unavailable)
    elif failure == "missing_dependency":
        real_import = builtins.__import__

        def unavailable_import(name, *args, **kwargs):
            if name in {"pymupdf", "fitz"}:
                raise ImportError("PDF rendering dependency unavailable")
            return real_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", unavailable_import)
    elif failure == "timeout":
        def timed_out(*args, **kwargs):
            raise RuntimeError("Tesseract process timeout")
        monkeypatch.setattr(pytesseract, "image_to_string", timed_out)
    else:
        monkeypatch.setattr(pytesseract, "image_to_string", lambda *a, **k: "")

    with pytest.raises(RuntimeError, match="第 2 页.*OCR"):
        adapter(tmp_path)._fetch_sync(path)


def test_sparse_text_above_scan_is_preserved_alongside_ocr(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "header.pdf", [("text", LONG_TEXT), ("scan", "Page marker")])
    monkeypatch.setattr(pytesseract, "image_to_string", lambda *a, **k: "扫描正文：金额 12800 元。")
    result = adapter(tmp_path)._fetch_sync(path)
    assert "Page marker" in result.raw_content
    assert "扫描正文：金额 12800 元。" in result.raw_content


def test_genuine_blank_page_does_not_require_ocr_or_hide_page_order(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "blank-in-middle.pdf", [
        ("text", LONG_TEXT), ("blank", ""), ("text", "Last page."),
    ])
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", str(tmp_path / "no-tesseract"))
    result = adapter(tmp_path)._fetch_sync(path)
    assert result.metadata["blank_pages"] == [2]
    assert result.metadata["ocr_used"] is False
    assert result.raw_content.index("## 第 2 页") < result.raw_content.index("Last page.")


def test_short_native_text_does_not_depend_on_ocr(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "short.pdf", [("text", "Amount: 12800.")])
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", str(tmp_path / "no-tesseract"))
    result = adapter(tmp_path)._fetch_sync(path)
    assert "Amount: 12800." in result.raw_content
    assert result.metadata["ocr_used"] is False


def test_exactly_white_scan_is_blank_without_calling_missing_ocr(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "white-scan.pdf", [("text", LONG_TEXT), ("white_scan", "")])
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", str(tmp_path / "no-tesseract"))
    result = adapter(tmp_path)._fetch_sync(path)
    assert result.metadata["blank_pages"] == [2]
    assert result.metadata["ocr_used"] is False
    assert "（空白页）" in result.raw_content


def test_only_blank_scanned_page_is_not_document_content(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "empty-scan.pdf", [("white_scan", "")])
    monkeypatch.setattr(pytesseract.pytesseract, "tesseract_cmd", str(tmp_path / "no-tesseract"))
    with pytest.raises(ValueError, match="PDF 未提取到正文"):
        adapter(tmp_path)._fetch_sync(path)
