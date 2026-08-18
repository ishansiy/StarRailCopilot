#!/usr/bin/env python3
"""Return the exact ADB transport state for one serial from `adb devices` output."""

import argparse
import sys


def device_state(serial: str, output: str) -> str:
    for raw_line in output.splitlines():
        columns = raw_line.split()
        if len(columns) >= 2 and columns[0] == serial:
            return columns[1]
    return "missing"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True)
    args = parser.parse_args()
    print(device_state(args.serial, sys.stdin.read()))


if __name__ == "__main__":
    main()
