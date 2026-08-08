# -*- coding: utf-8 -*-
"""Isolated OpenD authentication probe.

This module is launched as a short-lived subprocess. ``os._exit`` is
intentional: Futu's reconnect threads must never keep the probe alive.
"""

from __future__ import annotations

import os
import socket
import sys
from time import monotonic, sleep


def _finish(message: str, code: int) -> None:
    print(message, flush=True)
    os._exit(code)


def main() -> None:
    if len(sys.argv) != 3:
        _finish("UNAVAILABLE", 3)
    host = sys.argv[1]
    try:
        port = int(sys.argv[2])
    except ValueError:
        _finish("UNAVAILABLE", 3)

    try:
        with socket.create_connection((host, port), timeout=0.4):
            pass
    except OSError:
        _finish("UNAVAILABLE", 3)

    try:
        from futu import OpenQuoteContext

        context = OpenQuoteContext(host=host, port=port, is_async_connect=True)
        # Futu import takes about 1.2s on the 2 GB production host. Keep the
        # connect window short so the parent can still enforce a 2s hard stop.
        deadline = monotonic() + 0.45
        while monotonic() < deadline:
            if context._is_ready():
                _finish("READY", 0)
            sleep(0.05)
    except Exception:
        _finish("UNAVAILABLE", 3)
    _finish("VERIFICATION_REQUIRED", 2)


if __name__ == "__main__":
    main()
