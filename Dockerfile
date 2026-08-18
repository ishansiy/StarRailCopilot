ARG BASE_IMAGE=bgzerol/starrailcopilot:slim@sha256:fb3cc1d3d180f381c81e5a05683782fbbf02590613abf5f59c65b2f12762745f
ARG TAILSCALE_IMAGE=docker.io/tailscale/tailscale:stable@sha256:321ce041508c19079b57a28b6666c8d81ab0b08accc0a2585b3ab663d557ac24
FROM ${TAILSCALE_IMAGE} AS tailscale
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.source="https://github.com/ishansiy/StarRailCopilot" \
      org.opencontainers.image.description="StarRailCopilot image built from the ishansiy fork"

USER root
WORKDIR /app

COPY --from=tailscale /usr/local/bin/tailscale /usr/local/bin/tailscale
COPY --from=tailscale /usr/local/bin/tailscaled /usr/local/bin/tailscaled
COPY --from=tailscale /usr/local/bin/containerboot /usr/local/bin/containerboot

# 基础镜像提供 Python、ADB、Git 与图像处理运行库；应用源码使用当前 fork 的版本。
RUN find /app -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
COPY . /app

RUN python -m pip install --no-cache-dir -r requirements-in.txt \
    && command -v python \
    && command -v git \
    && command -v adb \
    && command -v base64 \
    && command -v timeout \
    && install -m 0755 deploy/docker-entrypoint.sh /usr/local/bin/starrail-entrypoint \
    && install -m 0755 deploy/tailscale-adb-forwarder.py /usr/local/bin/starrail-tailscale-forwarder \
    && install -m 0755 deploy/adb-device-state.py /usr/local/bin/starrail-adb-device-state \
    && install -m 0755 deploy/configure-adb-serial.py /usr/local/bin/starrail-configure-adb

ENV PORT=22367 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

EXPOSE 22367

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=5 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '22367') + '/', timeout=4)"]

ENTRYPOINT ["/usr/local/bin/starrail-entrypoint"]
CMD []
