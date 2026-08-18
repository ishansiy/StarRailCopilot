#!/bin/sh
set -eu

cd /app

if [ -z "${SRC_WEBUI_PASSWORD:-}" ]; then
  echo "SRC_WEBUI_PASSWORD 未配置，拒绝启动公开管理界面" >&2
  exit 64
fi

port="${PORT:-22367}"
data_dir="${SRC_DATA_DIR:-}"
tailscale_pid=""
forwarder_pid=""
pair_forwarder_pid=""
adb_retry_pid=""
app_pid=""

cleanup() {
  for pid in "$adb_retry_pid" "$pair_forwarder_pid" "$forwarder_pid" "$tailscale_pid" "$app_pid"; do
    if [ -n "$pid" ]; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
}

trap 'cleanup; exit 143' INT TERM

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

adb_state_dir="${SRC_ADB_STATE_DIR:-}"
if [ -z "$adb_state_dir" ] && [ -n "$data_dir" ]; then
  adb_state_dir="$data_dir/android"
fi
if [ -n "$adb_state_dir" ]; then
  mkdir -p "$adb_state_dir"
  if [ -d /root/.android ] && [ ! -L /root/.android ]; then
    if [ -z "$(find "$adb_state_dir" -mindepth 1 -print -quit)" ]; then
      cp -a /root/.android/. "$adb_state_dir/"
    fi
    rm -rf /root/.android
  fi
  if [ ! -e /root/.android ]; then
    ln -s "$adb_state_dir" /root/.android
  fi
fi

start_tailscale() {
  tailscale_target="${SRC_TAILSCALE_ADB_HOST:-}"
  tailscale_authkey="${TS_AUTHKEY:-${TAILSCALE_AUTHKEY:-}}"
  tailscale_state_dir="${TS_STATE_DIR:-${data_dir:-/tmp/starrail-copilot}/tailscale}"
  tailscale_socket="${TS_SOCKET:-/tmp/tailscale/tailscaled.sock}"

  state_present=false
  if [ -d "$tailscale_state_dir" ] && [ -n "$(find "$tailscale_state_dir" -mindepth 1 -print -quit 2>/dev/null)" ]; then
    state_present=true
  fi
  if [ -z "$tailscale_authkey" ] && [ "$state_present" != true ]; then
    if [ -n "$tailscale_target" ]; then
      echo "已配置 SRC_TAILSCALE_ADB_HOST，但缺少 TS_AUTHKEY 和持久化登录状态" >&2
      return 1
    fi
    echo "Tailscale 未配置，跳过 Tailnet ADB 转发"
    return 0
  fi

  mkdir -p "$tailscale_state_dir" "$(dirname "$tailscale_socket")"
  export TS_AUTHKEY="$tailscale_authkey"
  export TS_STATE_DIR="$tailscale_state_dir"
  export TS_SOCKET="$tailscale_socket"
  export TS_HOSTNAME="${TS_HOSTNAME:-starrail-copilot-modelscope}"
  export TS_USERSPACE=true
  export TS_AUTH_ONCE=true
  export TS_KUBE_SECRET=""

  /usr/local/bin/containerboot &
  tailscale_pid=$!
  connected=false
  attempt=0
  while [ "$attempt" -lt 90 ]; do
    if /usr/local/bin/tailscale --socket="$tailscale_socket" ip -4 >/dev/null 2>&1; then
      connected=true
      break
    fi
    if ! kill -0 "$tailscale_pid" 2>/dev/null; then
      break
    fi
    attempt=$((attempt + 1))
    sleep 1
  done
  if [ "$connected" != true ]; then
    echo "Tailscale 未能在 90 秒内加入 Tailnet" >&2
    return 1
  fi
  echo "Tailscale 已加入 Tailnet（userspace networking）"

  if [ -z "$tailscale_target" ]; then
    echo "未配置 SRC_TAILSCALE_ADB_HOST，仅保持 Tailnet 节点在线"
    return 0
  fi

  tailscale_target_port="${SRC_TAILSCALE_ADB_PORT:-5555}"
  tailscale_local_port="${SRC_TAILSCALE_ADB_LOCAL_PORT:-5555}"
  adb_serial="${SRC_ADB_SERIAL:-127.0.0.1:${tailscale_local_port}}"
  /usr/local/bin/starrail-tailscale-forwarder \
    --socket "$tailscale_socket" \
    --target-host "$tailscale_target" \
    --target-port "$tailscale_target_port" \
    --listen-port "$tailscale_local_port" &
  forwarder_pid=$!
  sleep 1
  if ! kill -0 "$forwarder_pid" 2>/dev/null; then
    echo "Tailnet ADB 本地转发器启动失败" >&2
    return 1
  fi
  /usr/local/bin/starrail-configure-adb --config-dir /app/config --serial "$adb_serial"

  pair_port="${SRC_TAILSCALE_ADB_PAIR_PORT:-}"
  pair_code="${SRC_TAILSCALE_ADB_PAIR_CODE:-}"
  if [ -n "$pair_port" ] || [ -n "$pair_code" ]; then
    if [ -z "$pair_port" ] || [ -z "$pair_code" ]; then
      echo "首次配对必须同时配置 SRC_TAILSCALE_ADB_PAIR_PORT 和 SRC_TAILSCALE_ADB_PAIR_CODE" >&2
      return 1
    fi
    pair_local_port="${SRC_TAILSCALE_ADB_PAIR_LOCAL_PORT:-37000}"
    /usr/local/bin/starrail-tailscale-forwarder \
      --socket "$tailscale_socket" \
      --target-host "$tailscale_target" \
      --target-port "$pair_port" \
      --listen-port "$pair_local_port" &
    pair_forwarder_pid=$!
    sleep 1
    if ! printf '%s\n' "$pair_code" | adb pair "127.0.0.1:${pair_local_port}" >/dev/null 2>&1; then
      echo "Tailnet ADB 配对失败；请刷新手机配对码和配对端口" >&2
      return 1
    fi
    echo "Tailnet ADB 首次配对成功"
    kill -TERM "$pair_forwarder_pid" 2>/dev/null || true
    wait "$pair_forwarder_pid" 2>/dev/null || true
    pair_forwarder_pid=""
  fi

  adb_retry_seconds="${SRC_TAILSCALE_ADB_RETRY_SECONDS:-15}"
  case "$adb_retry_seconds" in
    ''|*[!0-9]*|0)
      echo "SRC_TAILSCALE_ADB_RETRY_SECONDS 必须是正整数" >&2
      return 1
      ;;
  esac
  (
    previous_state="__initial__"
    while :; do
      adb connect "$adb_serial" >/dev/null 2>&1 || true
      adb_state="$(adb devices 2>/dev/null | awk -v serial="$adb_serial" '$1 == serial { print $2; exit }')"
      if [ "$adb_state" != "$previous_state" ]; then
        case "$adb_state" in
          device)
            echo "Tailnet ADB 设备已连接"
            ;;
          unauthorized)
            echo "Tailnet ADB 等待手机确认 RSA 调试授权" >&2
            ;;
          offline)
            echo "Tailnet ADB 设备离线，继续自动重连" >&2
            ;;
          *)
            echo "Tailnet ADB 设备暂未连接，继续自动重连" >&2
            ;;
        esac
      fi
      previous_state="$adb_state"
      sleep "$adb_retry_seconds"
    done
  ) &
  adb_retry_pid=$!
}

if ! start_tailscale; then
  if [ "${SRC_TAILSCALE_REQUIRED:-1}" = "1" ] || [ -n "${SRC_TAILSCALE_ADB_HOST:-}" ]; then
    cleanup
    exit 69
  fi
  echo "Tailscale 启动失败，但 SRC_TAILSCALE_REQUIRED=0，继续启动 WebUI" >&2
fi
unset TS_AUTHKEY TAILSCALE_AUTHKEY SRC_TAILSCALE_ADB_PAIR_CODE tailscale_authkey pair_code

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

python gui.py \
  --host 0.0.0.0 \
  --port "$port" \
  --key "$SRC_WEBUI_PASSWORD" &
app_pid=$!

set +e
wait "$app_pid"
status=$?
set -e
app_pid=""
cleanup
wait 2>/dev/null || true
exit "$status"
