"""Force UTF-8 on this process and on child interpreters.

Chinese Windows consoles otherwise default to a legacy code page, so titles
come back garbled. Reconfiguring the stdio wrappers fixes this process even
when it was started under a non-UTF-8 PYTHONIOENCODING.
"""

from __future__ import annotations

import io
import os
import sys


def force_utf8() -> None:
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONLEGACYWINDOWSSTDIO"] = "0"
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            try:
                wrapped = io.TextIOWrapper(
                    buffer,
                    encoding="utf-8",
                    errors="replace",
                    line_buffering=name != "stdin",
                    write_through=name != "stdin",
                )
                setattr(sys, name, wrapped)
                continue
            except Exception:
                pass
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                continue
