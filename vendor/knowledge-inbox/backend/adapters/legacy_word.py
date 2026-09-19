from __future__ import annotations

import asyncio
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import BinaryIO

from backend.adapters.base import FetchedContent, SourceAdapter


class LegacyWordAdapter(SourceAdapter):
    """Extract binary Word documents with antiword; never execute document macros."""

    source_type = "local_file"
    TIMEOUT_SECONDS = 30
    MAX_OUTPUT_BYTES = 8 * 1024 * 1024
    MAX_ERROR_BYTES = 64 * 1024
    _ole_header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

    @classmethod
    def detect(cls, value: str | Path) -> bool:
        return isinstance(value, Path) and value.suffix.lower() == ".doc"

    async def fetch(self, value: str | Path, **kwargs: object) -> FetchedContent:
        path = Path(value)
        content = await asyncio.to_thread(self._read, path)
        return FetchedContent(
            source_type=self.source_type,
            title=str(kwargs.get("title") or path.stem),
            raw_content=content,
            media_files=[str(path)],
            metadata={"document_format": "doc", "extractor": "antiword"},
        )

    def _read(self, path: Path) -> str:
        with path.open("rb") as source:
            if source.read(8) != self._ole_header:
                raise ValueError("DOC 格式无效：需要真正的 Word 97–2003 二进制文档")
        command = ["antiword", "-m", "UTF-8.txt", "-w", "0", str(path.resolve())]
        try:
            with subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, shell=False,
            ) as process:
                # Drain both pipes concurrently. Each reader retains only its limit + 1
                # bytes and kills the converter immediately if that limit is exceeded.
                with ThreadPoolExecutor(max_workers=2) as executor:
                    stdout = executor.submit(
                        self._bounded_output, process.stdout, process, self.MAX_OUTPUT_BYTES
                    )
                    stderr = executor.submit(
                        self._bounded_output, process.stderr, process, self.MAX_ERROR_BYTES
                    )
                    try:
                        process.wait(timeout=self.TIMEOUT_SECONDS)
                    except subprocess.TimeoutExpired as error:
                        process.kill()
                        process.wait()
                        raise RuntimeError(
                            f"DOC 提取超时（{self.TIMEOUT_SECONDS} 秒），请检查文件"
                        ) from error
                    output, errors = stdout.result(), stderr.result()
                returncode = process.returncode
        except FileNotFoundError as error:
            raise RuntimeError("DOC 提取需要 antiword；请在 Ubuntu 安装 antiword 软件包") from error
        if returncode:
            detail = errors.decode("utf-8", errors="replace").strip()[:500]
            raise ValueError(f"DOC 提取失败：文件损坏、加密或格式不受支持。{detail}")
        try:
            text = output.decode("utf-8").replace("\x0c", "\n\n").strip()
        except UnicodeDecodeError as error:
            raise ValueError("DOC 提取结果不是有效 UTF-8，请检查 antiword 字符映射") from error
        if not text:
            raise ValueError("DOC 未提取到正文：空白文档或仅包含图片")
        return text

    @staticmethod
    def _bounded_output(stream: BinaryIO, process: subprocess.Popen, limit: int) -> bytes:
        output = stream.read(limit + 1)
        if len(output) > limit:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            raise ValueError("DOC 提取输出超过解析上限，不能截断后继续入库")
        return output
