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
- `SRC_TAILSCALE_ADB_HOST`: Phone MagicDNS name or Tailscale IP.
- `SRC_TAILSCALE_ADB_PORT`: Phone wireless-debugging port.
- `SRC_TAILSCALE_ADB_LOCAL_PORT`: Local loopback port, normally `5555`.
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
