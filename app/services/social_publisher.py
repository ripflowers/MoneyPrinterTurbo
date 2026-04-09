from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from app.config import config
from app.thirdparty.social_auto_upload.sau_cli import (
    BilibiliVideoUploadRequest,
    DouyinVideoUploadRequest,
    KuaishouVideoUploadRequest,
    XiaohongshuVideoUploadRequest,
    upload_bilibili_video,
    upload_kuaishou_video,
    upload_video as upload_douyin_video,
    upload_xiaohongshu_video,
)
from app.thirdparty.social_auto_upload.uploader.douyin_uploader.main import (
    DOUYIN_PUBLISH_STRATEGY_IMMEDIATE,
    DOUYIN_PUBLISH_STRATEGY_SCHEDULED,
)
from app.thirdparty.social_auto_upload.uploader.ks_uploader.main import (
    KUAISHOU_PUBLISH_STRATEGY_IMMEDIATE,
    KUAISHOU_PUBLISH_STRATEGY_SCHEDULED,
)
from app.thirdparty.social_auto_upload.uploader.xiaohongshu_uploader.main import (
    XIAOHONGSHU_PUBLISH_STRATEGY_IMMEDIATE,
    XIAOHONGSHU_PUBLISH_STRATEGY_SCHEDULED,
)


SUPPORTED_PLATFORMS = {"douyin", "kuaishou", "xiaohongshu", "bilibili"}


@dataclass(slots=True)
class PublishResult:
    platform: str
    success: bool
    account_name: str = ""
    message: str = ""
    video_path: str = ""
    payload: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "success": self.success,
            "account_name": self.account_name,
            "message": self.message,
            "video_path": self.video_path,
            "payload": self.payload or {},
        }


class SocialPublisherService:
    def __init__(self) -> None:
        self._app_config = config.app

    @property
    def enabled(self) -> bool:
        return bool(self._app_config.get("social_auto_upload_enabled", False))

    @property
    def auto_upload(self) -> bool:
        return bool(self._app_config.get("social_auto_upload_auto_publish", False))

    @property
    def headless(self) -> bool:
        return bool(self._app_config.get("social_auto_upload_headless", True))

    @property
    def debug(self) -> bool:
        return bool(self._app_config.get("social_auto_upload_debug", False))

    def configured_platforms(self) -> list[str]:
        raw = self._app_config.get("social_auto_upload_platforms", []) or []
        platforms: list[str] = []
        for item in raw:
            name = str(item).strip().lower()
            if name in SUPPORTED_PLATFORMS and name not in platforms:
                platforms.append(name)
        return platforms

    def is_configured(self) -> bool:
        return self.enabled and len(self.configured_platforms()) > 0

    def _platform_config(self, platform: str) -> dict[str, Any]:
        return self._app_config.get(f"social_auto_upload_{platform}", {}) or {}

    def _build_title(self, video_subject: str, override_title: str = "") -> str:
        title = (override_title or video_subject or "").strip()
        return title[:80] if title else "视频作品"

    def _build_description(self, video_subject: str, video_script: str, tags: list[str], override_desc: str = "") -> str:
        base_desc = (override_desc or video_script or video_subject or "").strip()
        normalized_tags = []
        for tag in tags:
            cleaned = str(tag).strip().lstrip("#")
            if cleaned:
                normalized_tags.append(cleaned)
        tag_text = " ".join(f"#{tag}" for tag in normalized_tags)
        text = "\n\n".join(part for part in [base_desc, tag_text] if part)
        return text[:2200]

    def _parse_tags(self, raw_tags: Any) -> list[str]:
        if not raw_tags:
            return []
        if isinstance(raw_tags, list):
            return [str(tag).strip().lstrip("#") for tag in raw_tags if str(tag).strip()]
        return [part.strip().lstrip("#") for part in str(raw_tags).replace("，", ",").split(",") if part.strip()]

    def _parse_schedule(self, schedule_value: Any) -> datetime | int:
        if not schedule_value:
            return 0
        if isinstance(schedule_value, datetime):
            return schedule_value
        text = str(schedule_value).strip()
        if not text:
            return 0
        return datetime.fromisoformat(text)

    def _run_async(self, coro):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
        return asyncio.run(coro)

    def publish_video(
        self,
        video_path: str,
        video_subject: str,
        video_script: str = "",
        tags: list[str] | None = None,
        target_platforms: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.is_configured():
            return []

        if not os.path.isfile(video_path):
            return [
                PublishResult(
                    platform="unknown",
                    success=False,
                    message=f"video file not found: {video_path}",
                    video_path=video_path,
                ).to_dict()
            ]

        platforms = target_platforms or self.configured_platforms()
        normalized_tags = self._parse_tags(tags or [])
        results: list[dict[str, Any]] = []

        for platform in platforms:
            if platform not in SUPPORTED_PLATFORMS:
                results.append(
                    PublishResult(
                        platform=platform,
                        success=False,
                        message=f"unsupported platform: {platform}",
                        video_path=video_path,
                    ).to_dict()
                )
                continue

            platform_conf = self._platform_config(platform)
            account_name = str(platform_conf.get("account_name", "")).strip()
            if not account_name:
                results.append(
                    PublishResult(
                        platform=platform,
                        success=False,
                        message="missing account_name in config",
                        video_path=video_path,
                    ).to_dict()
                )
                continue

            title = self._build_title(video_subject, str(platform_conf.get("title", "")))
            desc = self._build_description(
                video_subject=video_subject,
                video_script=video_script,
                tags=normalized_tags,
                override_desc=str(platform_conf.get("desc", "")),
            )
            schedule = self._parse_schedule(platform_conf.get("schedule_time", ""))

            try:
                logger.info(f"publishing video to {platform}, account={account_name}")
                payload = self._publish_single(
                    platform=platform,
                    account_name=account_name,
                    video_path=video_path,
                    title=title,
                    desc=desc,
                    tags=normalized_tags,
                    schedule=schedule,
                    platform_conf=platform_conf,
                )
                results.append(
                    PublishResult(
                        platform=platform,
                        success=True,
                        account_name=account_name,
                        message="submitted",
                        video_path=video_path,
                        payload=payload,
                    ).to_dict()
                )
            except Exception as exc:
                logger.exception(f"failed to publish {video_path} to {platform}")
                results.append(
                    PublishResult(
                        platform=platform,
                        success=False,
                        account_name=account_name,
                        message=str(exc),
                        video_path=video_path,
                    ).to_dict()
                )

        return results

    def _publish_single(
        self,
        platform: str,
        account_name: str,
        video_path: str,
        title: str,
        desc: str,
        tags: list[str],
        schedule: datetime | int,
        platform_conf: dict[str, Any],
    ) -> dict[str, Any]:
        if platform == "douyin":
            publish_strategy = (
                DOUYIN_PUBLISH_STRATEGY_SCHEDULED if isinstance(schedule, datetime) else DOUYIN_PUBLISH_STRATEGY_IMMEDIATE
            )
            request = DouyinVideoUploadRequest(
                account_name=account_name,
                video_file=Path(video_path),
                title=title,
                description=desc,
                tags=tags,
                publish_date=schedule,
                thumbnail_file=Path(platform_conf["thumbnail_file"]).expanduser() if platform_conf.get("thumbnail_file") else None,
                product_link=str(platform_conf.get("product_link", "")),
                product_title=str(platform_conf.get("product_title", "")),
                publish_strategy=publish_strategy,
                debug=self.debug,
                headless=self.headless,
            )
            self._run_async(upload_douyin_video(request))
            return {
                "title": title,
                "description": desc,
                "tags": tags,
                "publish_strategy": publish_strategy,
            }

        if platform == "kuaishou":
            publish_strategy = (
                KUAISHOU_PUBLISH_STRATEGY_SCHEDULED if isinstance(schedule, datetime) else KUAISHOU_PUBLISH_STRATEGY_IMMEDIATE
            )
            request = KuaishouVideoUploadRequest(
                account_name=account_name,
                video_file=Path(video_path),
                title=title,
                description=desc,
                tags=tags,
                publish_date=schedule,
                thumbnail_file=Path(platform_conf["thumbnail_file"]).expanduser() if platform_conf.get("thumbnail_file") else None,
                publish_strategy=publish_strategy,
                debug=self.debug,
                headless=self.headless,
            )
            self._run_async(upload_kuaishou_video(request))
            return {
                "title": title,
                "description": desc,
                "tags": tags,
                "publish_strategy": publish_strategy,
            }

        if platform == "xiaohongshu":
            publish_strategy = (
                XIAOHONGSHU_PUBLISH_STRATEGY_SCHEDULED
                if isinstance(schedule, datetime)
                else XIAOHONGSHU_PUBLISH_STRATEGY_IMMEDIATE
            )
            request = XiaohongshuVideoUploadRequest(
                account_name=account_name,
                video_file=Path(video_path),
                title=title,
                description=desc,
                tags=tags,
                publish_date=schedule,
                thumbnail_file=Path(platform_conf["thumbnail_file"]).expanduser() if platform_conf.get("thumbnail_file") else None,
                publish_strategy=publish_strategy,
                debug=self.debug,
                headless=self.headless,
            )
            self._run_async(upload_xiaohongshu_video(request))
            return {
                "title": title,
                "description": desc,
                "tags": tags,
                "publish_strategy": publish_strategy,
            }

        if platform == "bilibili":
            tid = int(platform_conf.get("tid", 0) or 0)
            if tid <= 0:
                raise ValueError("bilibili.tid is required")
            request = BilibiliVideoUploadRequest(
                account_name=account_name,
                video_file=Path(video_path),
                title=title,
                description=desc,
                tid=tid,
                tags=tags,
                publish_date=schedule,
            )
            self._run_async(upload_bilibili_video(request))
            return {
                "title": title,
                "description": desc,
                "tags": tags,
                "tid": tid,
            }

        raise ValueError(f"unsupported platform: {platform}")


social_publisher_service = SocialPublisherService()


def publish_video(video_path: str, video_subject: str, video_script: str = "", tags: list[str] | None = None):
    return social_publisher_service.publish_video(
        video_path=video_path,
        video_subject=video_subject,
        video_script=video_script,
        tags=tags or [],
    )
