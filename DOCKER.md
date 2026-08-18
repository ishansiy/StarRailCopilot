# Docker deployment

## Basic startup

Copy `.env.docker.example` to `.env`, set `SRC_WEBUI_PASSWORD`, and run:

```sh
docker compose up -d --build
```

## Connect an Android phone through Tailscale

The image runs Tailscale in userspace networking mode, so it does not require
`/dev/net/tun` or a privileged container. Configure:

- `TS_AUTHKEY`: Tailscale auth key. Store this as a deployment Secret.
- `TS_HOSTNAME`: Name of this container in the tailnet.
- `SRC_TAILSCALE_ACCEPT_ROUTES`: Set to `1` only when the phone is reached
  through a Tailnet subnet router. This accepts routes advertised to the node.
- `SRC_TAILSCALE_ADB_HOST`: Phone MagicDNS name or Tailscale IP.
- `SRC_TAILSCALE_ADB_PORT`: Phone wireless-debugging port.
- `SRC_TAILSCALE_ADB_LOCAL_PORT`: Local loopback port, normally `5555`.
- `SRC_ADB_MANAGED_RESOLUTION`: Optional Android display size such as
  `720x1280`. The WebUI applies it immediately before a worker starts, then
  restores the phone's previous override (or its physical size) after the
  worker finishes, fails, or is stopped manually.
- `SRC_ADB_MANAGED_SCREEN_CROP`: Optional `LEFT,TOP,RIGHT,BOTTOM` crop for a
  phone whose hardware cutout keeps the game safe area smaller than the
  `1280x720` asset canvas. Pair it with a correspondingly larger managed
  resolution. For example, a `1332x720` landscape frame with a 52-pixel safe
  inset on the right uses `SRC_ADB_MANAGED_RESOLUTION=720x1332` and
  `SRC_ADB_MANAGED_SCREEN_CROP=0,0,52,0`. Frames of other sizes, including the
  portrait launcher during startup, are left unchanged.
- `SRC_TAILSCALE_ADB_PAIR_PORT`: One-time pairing port shown by Android.
- `SRC_TAILSCALE_ADB_PAIR_CODE`: One-time pairing code. Store this as a Secret
  and remove it after the first successful pairing.
- `SRC_TAILSCALE_ADB_CONNECT_TIMEOUT_SECONDS`: Timeout for one ADB connection
  attempt, normally `10`.
- `SRC_TAILSCALE_ADB_RETRY_SECONDS`: Reconnection interval, normally `15`.

StarRailCopilot is configured to use
`127.0.0.1:SRC_TAILSCALE_ADB_LOCAL_PORT`. The ADB port is never published by
Compose and should not be exposed to the public internet.

Android 11 and later require `adb pair` before the first `adb connect`. The image
can perform that first pairing through the tailnet and persists `/root/.android`
so the ADB identity survives restarts. Update `SRC_TAILSCALE_ADB_PORT` whenever
Android changes the wireless-debugging connection port; it is normally different
from the one-time pairing port.

The container keeps reconnecting in the background when the phone is offline or
restarts. A non-paired TCP ADB endpoint may still ask once for RSA debugging
authorization; that confirmation is different from Android wireless pairing.
Startup logs report subnet-route and TCP reachability separately without
printing credentials.
