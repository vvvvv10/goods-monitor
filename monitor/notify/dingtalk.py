"""钉钉自定义机器人推送（支持加签）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse

import requests


class DingTalkChannel:
    def __init__(self, webhook: str, secret: str = "", timeout: int = 10):
        if not webhook:
            raise ValueError("钉钉 webhook 不能为空")
        self.webhook = webhook
        self.secret = secret or ""
        self.timeout = timeout

    def _signed_url(self) -> str:
        if not self.secret:
            return self.webhook
        ts = str(round(time.time() * 1000))
        string_to_sign = f"{ts}\n{self.secret}"
        digest = hmac.new(
            self.secret.encode(), string_to_sign.encode(), hashlib.sha256
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(digest).decode())
        sep = "&" if "?" in self.webhook else "?"
        return f"{self.webhook}{sep}timestamp={ts}&sign={sign}"

    def send(self, title: str, markdown: str) -> None:
        resp = requests.post(
            self._signed_url(),
            json={"msgtype": "markdown", "markdown": {"title": title, "text": markdown}},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode") != 0:
            raise RuntimeError(f"钉钉推送失败: {data}")
