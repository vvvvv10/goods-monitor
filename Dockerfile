ARG BASE_IMAGE=python:3.11-slim-bookworm
FROM ${BASE_IMAGE}

# 国内构建默认使用阿里云 Debian 源、清华 PyPI 源；海外服务器可覆盖：
#   docker compose build --build-arg APT_MIRROR=deb.debian.org \
#     --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG APT_MIRROR=mirrors.aliyun.com
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PLAYWRIGHT_DOWNLOAD_HOST=

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" \
      /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true

# curl: 健康检查；supervisor: 同时托管监控循环和 Web 面板；中文字体：登录页截图
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tzdata curl supervisor fonts-noto-cjk \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
 && echo "$TZ" > /etc/timezone \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt \
 && export PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST}" \
 && playwright install --with-deps chromium --no-shell \
 && rm -rf /var/lib/apt/lists/*

COPY . .

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin monitor \
 && mkdir -p /app/data /var/log/supervisor \
 && chown -R monitor:monitor /app /var/log/supervisor

USER monitor
EXPOSE 8765

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8765/api/overview >/dev/null || exit 1

CMD ["supervisord", "-c", "/app/deploy/supervisord.conf", "-n"]
