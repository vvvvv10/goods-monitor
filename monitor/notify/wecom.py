"""企业微信群机器人推送。"""
from __future__ import annotations

import requests


class WeComChannel:
    def __init__(self, webhook: str, timeout: int = 10):
        if not webhook:
            raise ValueError("企业微信 webhook 不能为空")
        self.webhook = webhook
        self.timeout = timeout

    def send(self, title: str, markdown: str) -> None:
        content = markdown
        if len(content.encode("utf-8")) > 4000:
            content = content.encode("utf-8")[:3900].decode("utf-8", "ignore") + "\n…（内容过长已截断）"
        resp = requests.post(
            self.webhook,
            json={"msgtype": "markdown", "markdown": {"content": content}},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"企业微信推送失败: {data}")
