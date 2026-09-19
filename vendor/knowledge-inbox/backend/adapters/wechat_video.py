from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx

from backend.adapters.base import FetchedContent, SourceAdapter
from backend.processors.transcriber import Transcriber


class WeChatVideoAdapter(SourceAdapter):
    source_type = "wechat_video"
    _url_pattern = re.compile(
        r"https?://[^ ]*(?:channels\.weixin\.qq\.com|finder\.video\.qq\.com|weixin\.qq\.com/sph/)"
    )
    _unsupported_audio_suffixes = {".silk", ".slk"}

    @classmethod
    def detect(cls, value: str | Path) -> bool:
        if isinstance(value, Path):
            return (
                Transcriber.supports_file(value)
                or value.suffix.lower() in cls._unsupported_audio_suffixes
            )
        return bool(cls._url_pattern.match(value))

    async def fetch(self, value: str | Path, **kwargs: object) -> FetchedContent:
        if isinstance(value, Path):
            with value.open("rb") as source:
                header = source.read(16)
            if (
                value.suffix.lower() in self._unsupported_audio_suffixes
                or header.startswith((b"#!SILK_V3", b"\x02#!SILK_V3"))
            ):
                raise ValueError("SILK 音频暂不支持；请在微信接口选择 AMR，或先转换为 WAV/MP3")
            # This adapter also handles ordinary local audio/video files. Only
            # an explicit intake origin can identify them as WeChat material.
            source_type = "wechat" if kwargs.get("source_type") == "wechat" else "local_file"
            return FetchedContent(
                source_type=source_type,
                title=kwargs.get("title") or value.stem,
                media_files=[str(value)],
                metadata={"capture_mode": "uploaded_file"},
            )

        if not self.config.wechat_video_downloader_url:
            raise RuntimeError(
                "视频号链接抓取需要配置 wechat_video_downloader_url；也可直接上传原始视频"
            )

        base_url = self.config.wechat_video_downloader_url.rstrip("/")
        expected_dir = (self.config.data_dir / "originals" / "wechat_video").resolve()
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=30,
                trust_env=False,
            ) as client:
                response = await client.post("/api/task/create_channels", json={"url": str(value)})
                if response.status_code == 404:
                    task_id, file_path, title = await self._create_modern_task(
                        client, str(value), expected_dir
                    )
                else:
                    response.raise_for_status()
                    result = response.json()
                    if result.get("code") != 0:
                        message = str(result.get("msg") or "未知错误")
                        if "初始化客户端 socket" in message:
                            message = "下载器尚未连接微信客户端"
                        raise RuntimeError(
                            f"wx_channels_download 无法解析视频号链接：{message}"
                        )
                    task = result.get("data") or {}
                    task_id = str(task.get("id") or "")
                    file_path = Path(str(task.get("file_path") or "")).resolve()
                    if not task_id or not file_path.is_relative_to(expected_dir):
                        raise RuntimeError("wx_channels_download 返回了无效的下载任务")
                    title = await self._wait_for_download(client, task_id, file_path)
        except (httpx.ConnectError, httpx.TimeoutException) as error:
            raise RuntimeError(
                f"无法连接 wx_channels_download（{base_url}）；请先启动本地下载器"
            ) from error
        except httpx.HTTPStatusError as error:
            raise RuntimeError(
                f"wx_channels_download API 返回 HTTP {error.response.status_code}"
            ) from error

        return FetchedContent(
            source_type=self.source_type,
            source_url=str(value),
            title=str(kwargs.get("title") or title or file_path.stem).strip(),
            media_files=[str(file_path)],
            metadata={"capture_mode": "wx_channels_download", "download_task_id": task_id},
        )

    async def _create_modern_task(
        self, client: httpx.AsyncClient, source_url: str, expected_dir: Path
    ) -> tuple[str, Path, str]:
        params = {"url": source_url}
        if "/sph/" in source_url:
            params = {"eid": source_url.rstrip("/").rsplit("/", 1)[-1]}
        response = await client.get("/api/channels/feed/profile", params=params)
        response.raise_for_status()
        envelope = response.json()
        profile = envelope.get("data") or {}
        if envelope.get("code") != 0 or profile.get("errCode") != 0:
            message = str(profile.get("errMsg") or envelope.get("msg") or "获取详情失败")
            raise RuntimeError(f"wx_channels_download 无法解析视频号链接：{message}")
        obj = (profile.get("data") or {}).get("object")
        if not isinstance(obj, dict):
            raise RuntimeError("wx_channels_download 返回的视频详情缺少 object")

        spec = self._preferred_video_spec(obj)
        task_response = await client.post(
            "/api/v1/download_task/create",
            json={
                "objects": [
                    {
                        "platform": "wxchannels",
                        "content": obj,
                        "download_dir": str(expected_dir),
                        "auto_start": True,
                        "config": {
                            "existing_action": "overwrite",
                            "video_variant_spec": spec,
                            "spec": spec,
                        },
                    }
                ]
            },
        )
        task_response.raise_for_status()
        task_envelope = task_response.json()
        entries = ((task_envelope.get("data") or {}).get("tasks") or [])
        entry = entries[0] if entries else {}
        if task_envelope.get("code") != 0 or entry.get("code") != 0:
            message = str(entry.get("msg") or task_envelope.get("msg") or "创建下载任务失败")
            raise RuntimeError(f"wx_channels_download 无法创建下载任务：{message}")
        task = entry.get("data") or {}
        task_id = str(task.get("id") or "")
        if not task_id:
            raise RuntimeError("wx_channels_download 返回了无效的下载任务")
        return await self._wait_for_modern_download(client, task_id, expected_dir)

    @staticmethod
    def _preferred_video_spec(obj: dict) -> str:
        media = ((obj.get("objectDesc") or {}).get("media") or [{}])[0]
        specs = media.get("spec") or obj.get("spec") or []
        preferred = next(
            (item for item in specs if str(item.get("codingFormat") or "").lower() == "h264"),
            specs[0] if specs else {},
        )
        spec = str(preferred.get("fileFormat") or "").strip()
        if not spec:
            raise RuntimeError("wx_channels_download 返回的视频详情缺少可下载规格")
        return spec

    async def _wait_for_modern_download(
        self, client: httpx.AsyncClient, task_id: str, expected_dir: Path
    ) -> tuple[str, Path, str]:
        deadline = asyncio.get_running_loop().time() + (
            self.config.wechat_video_download_timeout_seconds
        )
        while asyncio.get_running_loop().time() < deadline:
            response = await client.get(
                "/api/v1/download_task/list", params={"task_id": task_id}
            )
            response.raise_for_status()
            envelope = response.json()
            task = envelope.get("data") or {}
            status = task.get("status")
            if status in (6, 7):
                message = str(task.get("error") or "wx_channels_download 下载视频失败")
                raise RuntimeError(message)
            if status == 5:
                files = task.get("files") or []
                file_path = Path(str((files[0] if files else {}).get("file_path") or "")).resolve()
                if not file_path.is_relative_to(expected_dir) or not file_path.is_file():
                    raise RuntimeError("wx_channels_download 返回了无效的已下载文件")
                return task_id, file_path, str(task.get("name") or "")
            await asyncio.sleep(0.5)
        raise RuntimeError("等待 wx_channels_download 下载视频超时")

    async def _wait_for_download(
        self, client: httpx.AsyncClient, task_id: str, file_path: Path
    ) -> str:
        deadline = asyncio.get_running_loop().time() + (
            self.config.wechat_video_download_timeout_seconds
        )
        while asyncio.get_running_loop().time() < deadline:
            response = await client.get("/api/task/list", params={"page_size": 200})
            response.raise_for_status()
            data = response.json().get("data") or {}
            task = next(
                (entry for entry in data.get("list") or [] if entry.get("id") == task_id),
                None,
            )
            if task and task.get("status") == "error":
                raise RuntimeError("wx_channels_download 下载视频失败")
            if task and task.get("status") == "done" and file_path.is_file():
                labels = ((task.get("meta") or {}).get("req") or {}).get("labels") or {}
                return str(labels.get("title") or "")
            await asyncio.sleep(0.5)
        raise RuntimeError("等待 wx_channels_download 下载视频超时")
