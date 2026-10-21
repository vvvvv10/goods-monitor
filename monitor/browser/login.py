"""扫码登录助手：打开浏览器人工登录一次，会话长期保存到 profile。

本地有图形界面时使用有头模式；服务器/容器内使用 --headless，
定时把登录页截图写到 data/login/<平台>.png，用 scp 下载后扫码即可。
"""
from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from .session import IN_CONTAINER

LOGIN_URLS = {
    "jd": "https://passport.jd.com/new/login.aspx",
    "taobao": "https://login.taobao.com/member/login.jhtml",
    "pdd": "https://mobile.yangkeduo.com/login.html",
    "xianyu": "https://www.goofish.com/",
}

LOGIN_TIPS = {
    "jd": "请用「京东 APP」或微信扫码登录（也可账号密码登录）。",
    "taobao": "请用「淘宝 APP」扫码登录，或账号密码登录。",
    "pdd": "请用「拼多多 APP」扫码登录；如果页面不对，可在浏览器地址栏打开 https://mobile.yangkeduo.com 后点登录。",
    "xianyu": "请点页面右上角「登录」，用「闲鱼 APP」或淘宝扫码登录。",
}

# 登录成功后一般会出现的关键 cookie（用于状态检测）
AUTH_COOKIES = {
    "jd": ("pt_key", "pt_pin"),
    "taobao": ("unb", "_nk_"),
    "pdd": ("pdd_user_id", "PDDAccessToken", "pdd_uid"),
    "xianyu": ("unb", "_nk_"),
}


def _browser_args() -> list:
    args = [
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if IN_CONTAINER:
        args += ["--no-sandbox", "--disable-dev-shm-usage"]
    return args


def _save_screenshot(page, path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=False)
        return True
    except Exception:
        return False


def login(platform: str, profiles_dir: Path, headless: bool = False,
          qr_out: str | None = None, timeout: int = 300,
          channel: str = "chrome") -> bool:
    url = LOGIN_URLS.get(platform)
    if not url:
        raise SystemExit(f"不支持的平台: {platform}")
    profile = profiles_dir / platform
    profile.mkdir(parents=True, exist_ok=True)
    use_channel = channel or None

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            headless=headless,
            channel=use_channel,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1280, "height": 860},
            args=_browser_args(),
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(url, timeout=60000)
        except Exception as exc:
            print(f"页面打开出错（可手动在浏览器里操作）: {exc}")

        if headless:
            shot = Path(qr_out) if qr_out else (profiles_dir.parent / "login" / f"{platform}.png")
            print("\n无头模式登录：登录页会每 3 秒刷新截图到")
            print(f"  {shot}")
            print("请在另一个终端下载该图片（scp），用对应 APP 扫码；")
            print(f"脚本会等待 {timeout} 秒，检测到登录 cookie 后自动保存并退出。\n")
            deadline = time.time() + timeout
            last_report = 0.0
            while time.time() < deadline:
                cookies = {c["name"] for c in ctx.cookies()}
                hit = [n for n in AUTH_COOKIES.get(platform, ()) if n in cookies]
                if hit:
                    print(f"✅ 检测到登录凭证: {', '.join(hit)}（登录成功）")
                    ctx.close()
                    print(f"已保存浏览器会话到 {profile}")
                    return True
                _save_screenshot(page, shot)
                now = time.time()
                if now - last_report >= 15:
                    left = int(deadline - now)
                    print(f"⏳ 等待扫码… 剩余 {left} 秒（截图: {shot}）", flush=True)
                    last_report = now
                time.sleep(3)
            print("⌛ 等待超时，未检测到登录 cookie。可重新运行本命令。")
            ctx.close()
            return False

        print(f"\n即将打开浏览器窗口，登录【{platform}】")
        print(LOGIN_TIPS[platform])
        print("⚠️  如果正在运行监控程序，请先停掉它（避免 profile 冲突）。")
        print("登录完成后，回到这个终端按回车键保存并退出。\n")
        input("⏎ 登录完成后，按回车保存… ")
        cookies = {c["name"] for c in ctx.cookies()}
        hit = [n for n in AUTH_COOKIES.get(platform, ()) if n in cookies]
        if hit:
            print(f"✅ 检测到登录凭证: {', '.join(hit)}（登录成功）")
        else:
            print("⚠️ 未检测到明确的登录 cookie。若页面显示已登录，一般也能用；")
            print("   若之后监控提示未登录，请重新运行本命令。")
        ctx.close()
    print(f"已保存浏览器会话到 {profile}（下次无需重复登录，除非登录过期）。")
    return bool(hit)


def check_login(platform: str, profiles_dir: Path, channel: str = "chrome") -> tuple:
    """返回 (是否已登录, 说明)。"""
    profile = profiles_dir / platform
    if not profile.exists():
        return False, "尚未登录过"
    with sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                headless=True,
                channel=channel or None,
                locale="zh-CN",
                args=["--no-first-run"] + (["--no-sandbox", "--disable-dev-shm-usage"]
                                           if IN_CONTAINER else []),
            )
        except Exception as exc:
            return False, f"profile 无法打开（可能被运行中的监控占用）: {exc}"
        try:
            cookies = {c["name"] for c in ctx.cookies()}
            hit = [n for n in AUTH_COOKIES.get(platform, ()) if n in cookies]
            if hit:
                return True, f"检测到 {', '.join(hit)}"
            return False, "未检测到登录 cookie"
        finally:
            ctx.close()
