"""Playwright 浏览器会话管理。

每个平台一个独立的持久化用户目录（profile），扫码登录一次后长期复用，
避免频繁登录触发风控。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from playwright.sync_api import BrowserContext, Playwright, sync_playwright

log = logging.getLogger("browser")

# 容器内 Chromium 通常无法使用内核沙箱，需要显式关闭。
IN_CONTAINER = Path("/.dockerenv").exists() or os.environ.get("DEAL_MONITOR_NO_SANDBOX") == "1"


class BrowserManager:
    def __init__(self, profiles_dir: Path, headless: bool = True,
                 channel: str = "chrome", nav_timeout_ms: int = 30000):
        self.profiles_dir = Path(profiles_dir)
        self.headless = headless
        self.channel = channel or None
        self.nav_timeout_ms = nav_timeout_ms
        self._pw: Optional[Playwright] = None
        self._contexts: dict[str, BrowserContext] = {}

    def _start(self) -> None:
        if self._pw is None:
            self._pw = sync_playwright().start()

    def context(self, platform: str) -> BrowserContext:
        ctx = self._contexts.get(platform)
        if ctx is None:
            self._start()
            profile = self.profiles_dir / platform
            profile.mkdir(parents=True, exist_ok=True)
            log.info("启动浏览器 profile=%s headless=%s", profile.name, self.headless)
            assert self._pw is not None
            args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ]
            if IN_CONTAINER:
                args += ["--no-sandbox", "--disable-dev-shm-usage"]
            ctx = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=self.headless,
                channel=self.channel,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                viewport={"width": 1366, "height": 900},
                args=args,
            )
            ctx.set_default_navigation_timeout(self.nav_timeout_ms)
            ctx.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
            self._contexts[platform] = ctx
        return ctx

    def new_page(self, platform: str):
        return self.context(platform).new_page()

    def close(self) -> None:
        for ctx in self._contexts.values():
            try:
                ctx.close()
            except Exception:
                pass
        self._contexts.clear()
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None
