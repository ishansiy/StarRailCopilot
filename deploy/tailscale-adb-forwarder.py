#!/usr/bin/env python3
"""Expose a Tailnet TCP target on localhost through `tailscale nc`."""

import argparse
import socket
import socketserver
import subprocess
import threading


class TailnetForwardHandler(socketserver.BaseRequestHandler):
    def handle(self):
        server = self.server
        process = subprocess.Popen(
            [
                server.tailscale_bin,
                f"--socket={server.tailscale_socket}",
                "nc",
                server.target_host,
                str(server.target_port),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )

        def socket_to_process():
            try:
                while data := self.request.recv(64 * 1024):
                    process.stdin.write(data)
            except (BrokenPipeError, ConnectionError, OSError):
                pass
            finally:
                if process.stdin:
                    process.stdin.close()

        upload = threading.Thread(target=socket_to_process, daemon=True)
        upload.start()
        try:
            while True:
                data = process.stdout.read(64 * 1024)
                if not data:
                    break
                self.request.sendall(data)
        except (BrokenPipeError, ConnectionError, OSError):
            pass
        finally:
            try:
                self.request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.request.close()
            if process.poll() is None:
                process.terminate()
            upload.join(timeout=2)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


class ThreadingForwardServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def parse_port(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", type=parse_port, required=True)
    parser.add_argument("--listen-port", type=parse_port, required=True)
    parser.add_argument("--tailscale-bin", default="/usr/local/bin/tailscale")
    args = parser.parse_args()

    with ThreadingForwardServer(("127.0.0.1", args.listen_port), TailnetForwardHandler) as server:
        server.tailscale_socket = args.socket
        server.target_host = args.target_host
        server.target_port = args.target_port
        server.tailscale_bin = args.tailscale_bin
        print(f"Tailnet ADB forwarder listening on 127.0.0.1:{args.listen_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
