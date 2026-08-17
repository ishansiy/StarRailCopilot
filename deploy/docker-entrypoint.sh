#!/bin/sh
set -eu

cd /app

if [ -z "${SRC_WEBUI_PASSWORD:-}" ]; then
  echo "SRC_WEBUI_PASSWORD 未配置，拒绝启动公开管理界面" >&2
  exit 64
fi

port="${PORT:-22367}"
data_dir="${SRC_DATA_DIR:-}"

if [ -n "$data_dir" ]; then
  mkdir -p "$data_dir/config" "$data_dir/log"

  # ModelScope 的镜像层不可持久化；只在持久目录为空时复制镜像默认配置。
  if [ -d /app/config ] && [ ! -L /app/config ]; then
    if [ -z "$(find "$data_dir/config" -mindepth 1 -print -quit)" ]; then
      cp -a /app/config/. "$data_dir/config/"
    fi
    rm -rf /app/config
  fi

  if [ -d /app/log ] && [ ! -L /app/log ]; then
    rm -rf /app/log
  fi

  ln -s "$data_dir/config" /app/config
  ln -s "$data_dir/log" /app/log
else
  mkdir -p /app/config /app/log
fi

if [ ! -f /app/config/deploy.yaml ]; then
  python -m deploy.set \
    Repository=global \
    Branch=master \
    GitExecutable=git \
    AutoUpdate=false \
    PythonExecutable=python \
    InstallDependencies=false \
    AdbExecutable=adb \
    ReplaceAdb=false \
    AutoConnect=false \
    EnableReload=false \
    CheckUpdateInterval=0 \
    AutoRestartTime=null \
    WebuiHost=0.0.0.0 \
    WebuiPort="$port" \
    Language=zh-CN \
    Password=null
fi

exec python gui.py \
  --host 0.0.0.0 \
  --port "$port" \
  --key "$SRC_WEBUI_PASSWORD"
