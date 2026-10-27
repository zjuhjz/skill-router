"""Run a skill entrypoint. POSIX process groups; Windows Job Objects."""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    spawn_error: str | None = None


class CommandError(RuntimeError):
    pass


def resolve_command(argv: list[str]) -> list[str]:
    resolved: list[str] = []
    for part in argv:
        if part == "$PYTHON":
            resolved.append(sys.executable)
        elif part == "$NODE":
            found = shutil.which("node")
            if not found:
                raise CommandError("$NODE was not found on PATH")
            resolved.append(found)
        elif part == "$UV":
            found = shutil.which("uv")
            if not found:
                raise CommandError("$UV was not found on PATH")
            resolved.append(found)
        else:
            resolved.append(str(part))
    if not resolved:
        raise CommandError("empty command")
    return resolved


def _kill_posix(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        proc.wait(timeout=5)


def _win_job(proc: subprocess.Popen):
    """Put the process in a Job Object that dies with the job. No pywin32."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        return None

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    kernel.SetInformationJobObject(
        job,
        9,  # JobObjectExtendedLimitInformation
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    handle = kernel.OpenProcess(0x1F0FFF, False, proc.pid)  # PROCESS_ALL_ACCESS
    if handle:
        kernel.AssignProcessToJobObject(job, handle)
        kernel.CloseHandle(handle)
    return job


def _kill_windows(proc: subprocess.Popen, job) -> None:
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    if job:
        kernel.TerminateJobObject(job, 1)
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def run_command(
    argv: list[str],
    *,
    cwd: Path,
    request: dict,
    env: dict[str, str],
    timeout_s: float,
) -> ProcResult:
    try:
        command = resolve_command(argv)
    except CommandError as exc:
        return ProcResult(returncode=3, stdout="", stderr=str(exc), spawn_error=str(exc))
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=(os.name != "nt"),
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return ProcResult(returncode=3, stdout="", stderr=str(exc), spawn_error=str(exc))
    job = _win_job(proc)
    payload = json.dumps(request, ensure_ascii=False)
    try:
        stdout, stderr = proc.communicate(payload, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            _kill_windows(proc, job)
        else:
            _kill_posix(proc)
        try:
            stdout, stderr = proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        return ProcResult(
            returncode=proc.returncode if proc.returncode is not None else -9,
            stdout=stdout or "",
            stderr=stderr or "",
            timed_out=True,
        )
    return ProcResult(returncode=proc.returncode or 0, stdout=stdout or "", stderr=stderr or "")


def _ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def endpoint_class(url: str) -> str:
    """loopback (allowed), blocked, or public."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return "loopback"
    if not host:
        return "blocked"
    literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return "blocked" if _ip_blocked(literal) or literal.is_loopback else "public"
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return "blocked"
    for info in infos:
        addr = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return "blocked"
        if _ip_blocked(ip):
            return "blocked"
    return "public"


def http_allowed(url: str, *, third_party: bool) -> str | None:
    kind = endpoint_class(url)
    if kind == "blocked":
        return "refused non-local or link-local HTTP endpoint"
    if kind == "public" and not third_party:
        return "external HTTP endpoints require side_effects third_party_service and user confirmation"
    if kind == "public" and third_party:
        return None
    if kind == "loopback":
        return None
    return "refused HTTP endpoint"


def run_http(url: str, request: dict, *, timeout_s: float, third_party: bool) -> ProcResult:
    reason = http_allowed(url, third_party=third_party)
    if reason:
        body = json.dumps(
            {"ok": False, "error": {"code": "UPSTREAM_ERROR", "message": reason}},
            ensure_ascii=False,
        )
        return ProcResult(returncode=2, stdout=body, stderr=reason)
    data = json.dumps(request, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            raw = response.read()
            status = getattr(response, "status", 200)
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        status = exc.code
    except Exception as exc:
        body = json.dumps(
            {"ok": False, "error": {"code": "UPSTREAM_ERROR", "message": str(exc)[:300]}},
            ensure_ascii=False,
        )
        return ProcResult(returncode=2, stdout=body, stderr=str(exc))
    text = raw.decode("utf-8", errors="replace")
    if status != 200:
        if text.strip().startswith("{"):
            return ProcResult(returncode=2, stdout=text, stderr=f"HTTP {status}")
        body = json.dumps(
            {"ok": False, "error": {"code": "UPSTREAM_ERROR", "message": f"HTTP {status}"}},
            ensure_ascii=False,
        )
        return ProcResult(returncode=2, stdout=body, stderr=f"HTTP {status}")
    return ProcResult(returncode=0, stdout=text, stderr="")
