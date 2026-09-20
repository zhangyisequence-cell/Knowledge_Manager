from __future__ import annotations

import asyncio
from contextlib import ExitStack, closing
from datetime import date, datetime, time
from pathlib import Path
from zipfile import ZipFile

from backend.adapters.base import FetchedContent, SourceAdapter


class SpreadsheetAdapter(SourceAdapter):
    source_type = "spreadsheet"
    MAX_CELLS = 250_000
    MAX_COLUMNS = 256
    MAX_ROWS = 50_000
    MAX_EXPANDED_BYTES = 64 * 1024 * 1024

    @classmethod
    def detect(cls, value: str | Path) -> bool:
        return isinstance(value, Path) and value.suffix.lower() in {".xlsx", ".xlsm", ".xls"}

    async def fetch(self, value: str | Path, **kwargs: object) -> FetchedContent:
        path = Path(value)
        content = await asyncio.to_thread(self._read, path)
        return FetchedContent(
            source_type=self.source_type,
            title=str(kwargs.get("title") or path.stem),
            raw_content=content,
            media_files=[str(path)],
        )

    def _read(self, path: Path) -> str:
        try:
            sheets = self._legacy(path) if path.suffix.lower() == ".xls" else self._modern(path)
            blocks = []
            scanned = 0
            with closing(sheets):
                for name, rows in sheets:
                    rendered = []
                    width = 0
                    with closing(rows):
                        for index, values in enumerate(rows, 1):
                            scanned += len(values)
                            if (index > self.MAX_ROWS or len(values) > self.MAX_COLUMNS
                                    or scanned > self.MAX_CELLS):
                                raise ValueError("Excel 超过解析上限，请拆分工作簿；未截断入库")
                            if any(value != "" for value in values):
                                width = max(width, len(values))
                                rendered.append((index, values))
                    if rendered:
                        from openpyxl.utils import get_column_letter

                        header = ["行"] + [get_column_letter(i) for i in range(1, width + 1)]
                        lines = [self._row(header), self._row(["---"] * len(header))]
                        for index, values in rendered:
                            cells = [str(index), *values, *([""] * (width - len(values)))]
                            lines.append(self._row(cells))
                        blocks.append(f"## 工作表：{self._escape(name)}\n\n" + "\n".join(lines))
            if not blocks:
                raise ValueError("Excel 未提取到有效单元格内容")
            return "Excel 按原工作表、行列位置提取；公式不会重新计算。\n\n" + "\n\n".join(blocks)
        except Exception as error:
            raise ValueError(f"Excel 解析失败：{error}") from error

    def _dimensions(self, rows: int, columns: int) -> None:
        if rows > self.MAX_ROWS or columns > self.MAX_COLUMNS or rows * columns > self.MAX_CELLS:
            raise ValueError("Excel 超过解析上限，请拆分工作簿；未截断入库")

    def _modern(self, path: Path):
        from openpyxl import load_workbook

        with ZipFile(path) as archive:
            if sum(info.file_size for info in archive.infolist()) > self.MAX_EXPANDED_BYTES:
                raise ValueError("Excel 解压内容超过 64 MiB 上限，请拆分文件")
        with ExitStack() as stack:
            formula_file = stack.enter_context(path.open("rb"))
            formulas = load_workbook(formula_file, read_only=True, data_only=False, keep_links=False)
            stack.callback(formulas.close)
            cache_file = stack.enter_context(path.open("rb"))
            cached = load_workbook(cache_file, read_only=True, data_only=True, keep_links=False)
            stack.callback(cached.close)
            for sheet in formulas:
                self._dimensions(sheet.max_row or 0, sheet.max_column or 0)
                # Third-party exporters may understate dimensions. Scan actual XML rows.
                sheet.reset_dimensions()
                cached[sheet.title].reset_dimensions()

                def rows(sheet=sheet):
                    with closing(sheet.iter_rows()) as source, closing(
                        cached[sheet.title].iter_rows()
                    ) as results:
                        for source_row, value_row in zip(source, results):
                            values = []
                            for cell, cache in zip(source_row, value_row):
                                if cell.data_type == "f":
                                    result = "未缓存，未计算" if cache.value is None else (
                                        f"缓存结果（未重新计算）：{self._value(cache.value)}"
                                    )
                                    values.append(f"公式：{self._formula(cell.value)}；{result}")
                                else:
                                    values.append(self._value(cell.value))
                            yield values

                yield sheet.title, rows()

    def _legacy(self, path: Path):
        import xlrd

        with xlrd.open_workbook(path, on_demand=True) as workbook:
            for sheet in workbook.sheets():
                self._dimensions(sheet.nrows, sheet.ncols)

                def rows(sheet=sheet):
                    for row_index in range(sheet.nrows):
                        values = []
                        for cell in sheet.row(row_index):
                            if cell.ctype == xlrd.XL_CELL_DATE:
                                value = xlrd.xldate_as_datetime(cell.value, workbook.datemode)
                            elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                                value = bool(cell.value)
                            elif cell.ctype == xlrd.XL_CELL_ERROR:
                                value = xlrd.error_text_from_code.get(cell.value, "#ERROR")
                            else:
                                value = cell.value
                            values.append(self._value(value))
                        yield values

                yield sheet.name + "（XLS 值含已存储的公式结果，不重新计算）", rows()

    @staticmethod
    def _formula(value: object) -> str:
        from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula

        if isinstance(value, str):
            return value
        if isinstance(value, ArrayFormula):
            return f"数组范围 {value.ref}：{value.text}"
        if isinstance(value, DataTableFormula):
            return "数据表运算：" + ", ".join(f"{key}={part}" for key, part in dict(value).items())
        raise ValueError("Excel 包含无法识别的公式类型")

    @staticmethod
    def _value(value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("|", "\\|").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")

    @classmethod
    def _row(cls, values: list[str]) -> str:
        return "| " + " | ".join(cls._escape(value) for value in values) + " |"
