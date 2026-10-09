"""Offline check: is the network really off, and does Tanaw's guard block connections?

Steps (run with Wi-Fi turned off for the real offline test):

    .venv\\Scripts\\python.exe scripts\\check_offline.py --prove-offline

1. Lists network adapters that Windows reports as "Up" (read-only query).
2. Installs Tanaw's network guard and deliberately connects to a public address
   (1.1.1.1:443). PASS = the guard refused it inside Python, before any packet.
3. With --prove-offline only: WITHOUT the guard, tries the same connection for
   3 s. PASS = it fails, i.e. the computer is really offline. If the network is
   on, this makes a real connection attempt, so it only runs when asked.

Then run Tanaw itself; its startup prints and logs its own guard self-test.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys

from tanaw import netguard

PUBLIC = netguard.SELF_TEST_ADDRESS


def adapters_up() -> list[str]:
    command = [
        "powershell", "-NoProfile", "-Command",
        "Get-NetAdapter | Where-Object Status -eq 'Up' | "
        "ForEach-Object { $_.Name + ' (' + $_.InterfaceDescription + ')' }",
    ]
    try:
        out = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"(could not query adapters: {exc})"]
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def unguarded_connect_fails(timeout_s: float = 3.0) -> tuple[bool, str]:
    try:
        with socket.create_connection(PUBLIC, timeout=timeout_s):
            return False, "connected: the computer is ONLINE"
    except OSError as exc:
        return True, f"failed as expected ({type(exc).__name__}: {exc})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prove-offline", action="store_true",
                        help="Also try an unguarded connection (only with Wi-Fi off)")
    args = parser.parse_args(argv)

    print("1. Network adapters Windows reports as Up:")
    up = adapters_up()
    for name in up or ["(none)"]:
        print(f"   - {name}")

    results: dict[str, bool] = {}
    if args.prove_offline:
        ok, detail = unguarded_connect_fails()
        results["offline (unguarded connect to public address fails)"] = ok
        print(f"\n3. Unguarded connect to {PUBLIC[0]}:{PUBLIC[1]}: {detail}")

    netguard.install()
    blocked = netguard.self_test()
    results["netguard blocks connect to public address"] = blocked
    print(f"\n2. Guarded connect to {PUBLIC[0]}:{PUBLIC[1]}: "
          f"{'blocked by netguard' if blocked else 'NOT blocked'}")

    print("\nResult:")
    for check, ok in results.items():
        print(f"   {'PASS' if ok else 'FAIL'}  {check}")
    if not args.prove_offline:
        print("   (not run) offline proof: turn Wi-Fi off and re-run with --prove-offline")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
