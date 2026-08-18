import json
import os
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from module.device.method.adb_shell_patch import shell_with_check_okay


DEPLOY_DIR = Path(__file__).resolve().parent


class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while data := self.request.recv(64 * 1024):
            self.request.sendall(data)


class ContainerSupportTest(unittest.TestCase):
    def test_compose_defers_keep_awake_default_to_runtime_crop_policy(self):
        compose = (DEPLOY_DIR.parent / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn(
            "SRC_ADB_MANAGED_KEEP_AWAKE: ${SRC_ADB_MANAGED_KEEP_AWAKE:-}",
            compose,
        )

    def test_entrypoint_keeps_reconnecting_adb(self):
        entrypoint = (DEPLOY_DIR / "docker-entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn('set --accept-routes=true', entrypoint)
        self.assertIn('SRC_TAILSCALE_ACCEPT_ROUTES 只允许', entrypoint)
        self.assertIn('Tailnet ADB 目标路由可达', entrypoint)
        self.assertIn('Tailnet ADB TCP 端口可达', entrypoint)
        self.assertIn('while :; do', entrypoint)
        self.assertIn('timeout "$adb_connect_timeout" adb connect', entrypoint)
        self.assertIn('SRC_ADB_PRIVATE_KEY_B64', entrypoint)
        self.assertIn('SRC_ADB_PRIVATE_KEY_B64_PART_1', entrypoint)
        self.assertIn('SRC_ADB_PRIVATE_KEY_B64_PART_4', entrypoint)
        self.assertIn('unset adb_private_key_single adb_private_key_parts adb_private_key_b64', entrypoint)
        self.assertIn('starrail-adb-device-state --serial "$adb_serial"', entrypoint)
        self.assertNotIn('adb -s "$adb_serial" get-state', entrypoint)
        self.assertNotIn('| awk', entrypoint)
        self.assertIn('Tailnet ADB 等待手机确认 RSA 调试授权', entrypoint)
        self.assertNotIn('while [ "$retry" -lt 60 ]', entrypoint)

    def test_stream_shell_keeps_the_requested_transport_timeout(self):
        class FakeConnection:
            def __init__(self):
                self.commands = []
                self.checked = False

            def send_command(self, command):
                self.commands.append(command)

            def check_okay(self):
                self.checked = True

        class FakeDevice:
            def __init__(self):
                self.timeout = None
                self.connection = FakeConnection()

            def open_transport(self, timeout=None):
                self.timeout = timeout
                return self.connection

        device = FakeDevice()
        connection = shell_with_check_okay(
            device,
            ["screencap", "-p"],
            stream=True,
            timeout=0.1,
        )

        self.assertIs(connection, device.connection)
        self.assertEqual(device.timeout, 0.1)
        self.assertEqual(device.connection.commands, ["shell:screencap -p"])
        self.assertTrue(device.connection.checked)

    def test_adb_device_state_parser_distinguishes_authorization_states(self):
        parser = DEPLOY_DIR / "adb-device-state.py"
        samples = {
            "device": "List of devices attached\n127.0.0.1:5555\tdevice product:x model:y\n",
            "unauthorized": "List of devices attached\n127.0.0.1:5555\tunauthorized transport_id:1\n",
            "offline": "List of devices attached\n127.0.0.1:5555\toffline transport_id:1\n",
            "missing": "List of devices attached\n\n",
        }
        for expected, output in samples.items():
            with self.subTest(expected=expected):
                result = subprocess.run(
                    [sys.executable, str(parser), "--serial", "127.0.0.1:5555"],
                    input=output,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.stdout.strip(), expected)

    def test_configure_adb_serial_updates_template_and_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = {"Alas": {"Emulator": {"Serial": "auto"}}, "Other": {"keep": True}}
            for name in ("template.json", "profile.json"):
                (root / name).write_text(json.dumps(original), encoding="utf-8")
            (root / "invalid.json").write_text("not-json", encoding="utf-8")

            subprocess.run(
                [sys.executable, str(DEPLOY_DIR / "configure-adb-serial.py"), "--config-dir", str(root), "--serial", "127.0.0.1:5555"],
                check=True,
                capture_output=True,
                text=True,
            )

            for name in ("template.json", "profile.json"):
                configured = json.loads((root / name).read_text(encoding="utf-8"))
                self.assertEqual(configured["Alas"]["Emulator"]["Serial"], "127.0.0.1:5555")
                self.assertTrue(configured["Other"]["keep"])
            self.assertEqual((root / "invalid.json").read_text(encoding="utf-8"), "not-json")

    @unittest.skipIf(os.name == "nt", "executable shebang test runs in Linux CI")
    def test_tailnet_forwarder_proxies_a_tcp_stream(self):
        with socketserver.ThreadingTCPServer(("127.0.0.1", 0), EchoHandler) as echo:
            echo_thread = threading.Thread(target=echo.serve_forever, daemon=True)
            echo_thread.start()

            with tempfile.TemporaryDirectory() as directory:
                fake = Path(directory) / "tailscale"
                fake.write_text(
                    """#!/usr/bin/env python3
import socket, sys, threading
s = socket.create_connection((sys.argv[-2], int(sys.argv[-1])))
def upload():
    while data := sys.stdin.buffer.read(65536):
        s.sendall(data)
    s.shutdown(socket.SHUT_WR)
threading.Thread(target=upload, daemon=True).start()
while data := s.recv(65536):
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
""",
                    encoding="utf-8",
                )
                fake.chmod(0o755)

                with socket.socket() as reservation:
                    reservation.bind(("127.0.0.1", 0))
                    listen_port = reservation.getsockname()[1]

                forwarder = subprocess.Popen(
                    [
                        sys.executable,
                        str(DEPLOY_DIR / "tailscale-adb-forwarder.py"),
                        "--socket",
                        "/tmp/test-tailscale.sock",
                        "--target-host",
                        "127.0.0.1",
                        "--target-port",
                        str(echo.server_address[1]),
                        "--listen-port",
                        str(listen_port),
                        "--tailscale-bin",
                        str(fake),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    deadline = time.time() + 5
                    while time.time() < deadline:
                        try:
                            with socket.create_connection(("127.0.0.1", listen_port), timeout=0.2) as client:
                                client.sendall(b"tailnet-adb")
                                client.shutdown(socket.SHUT_WR)
                                self.assertEqual(client.recv(64), b"tailnet-adb")
                                break
                        except OSError:
                            time.sleep(0.05)
                    else:
                        self.fail("forwarder did not start")
                finally:
                    forwarder.terminate()
                    forwarder.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
