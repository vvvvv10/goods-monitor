"""通用 webhook 推送：POST {"title": ..., "text": ...}，方便对接 Bark / 自建服务等。"""
from __future__ import annotations

import requests


class WebhookChannel:
    def __init__(self, url: str, headers: dict | None = None, timeout: int = 10):
        if not url:
            raise ValueError("webhook url 不能为空")
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout

    def send(self, title: str, markdown: str) -> None:
        resp = requests.post(
            self.url,
            json={"title": title, "text": markdown},
            headers=self.headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()
