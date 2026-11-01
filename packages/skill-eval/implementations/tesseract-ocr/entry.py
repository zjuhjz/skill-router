#!/usr/bin/env python3
from __future__ import annotations

import csv
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path


def respond(payload: dict, exit_code: int = 0) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False)
    raise SystemExit(exit_code)


def fail(code: str, message: str, exit_code: int = 2) -> None:
    respond({"ok": False, "error": {"code": code, "message": message, "details": {}}}, exit_code)


def resolve_file(request: dict) -> Path:
    raw = str((request.get("input") or {}).get("file") or "").strip()
    if not raw:
        fail("INVALID_INPUT", "file is required")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(str((request.get("context") or {}).get("client_cwd") or ".")) / path
    path = path.resolve()
    if not path.is_file():
        fail("NOT_FOUND", "image file does not exist")
    return path


def language_code(language: str) -> str:
    normalized = language.lower().replace("_", "-")
    if normalized in {"zh-cn", "zh-hans"}:
        return "chi_sim"
    if normalized in {"zh-tw", "zh-hk", "zh-hant"}:
        return "chi_tra"
    return "eng" if normalized.startswith("en") or normalized == "und" else normalized.split("-", 1)[0]


def main() -> None:
    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        fail("INVALID_INPUT", "stdin must contain one JSON request")
    executable = shutil.which("tesseract")
    if not executable:
        fail("DEPENDENCY_MISSING", "tesseract binary is not installed", 3)
    image = resolve_file(request)
    requested_language = str((request.get("input") or {}).get("language") or "und")
    command = [executable, str(image), "stdout", "-l", language_code(requested_language), "tsv"]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=55, check=False)
    except subprocess.TimeoutExpired:
        fail("TIMEOUT", "tesseract timed out")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    if completed.returncode != 0:
        code = "DEPENDENCY_MISSING" if "Failed loading language" in stderr else "INTERNAL"
        fail(code, stderr[-500:] or "tesseract failed", 3 if code == "DEPENDENCY_MISSING" else 2)
    tsv = completed.stdout.decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(tsv), delimiter="\t"))
    page_width = max((int(row["width"]) for row in rows if row.get("level") == "1"), default=0)
    page_height = max((int(row["height"]) for row in rows if row.get("level") == "1"), default=0)
    lines: dict[tuple[str, str, str], list[str]] = {}
    blocks = []
    for row in rows:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        key = (str(row.get("block_num")), str(row.get("par_num")), str(row.get("line_num")))
        lines.setdefault(key, []).append(text)
        confidence_raw = str(row.get("conf") or "-1")
        confidence = max(0.0, min(1.0, float(confidence_raw) / 100))
        block = {"text": text, "confidence": confidence}
        if page_width and page_height:
            block["bbox"] = [
                int(row["left"]) / page_width,
                int(row["top"]) / page_height,
                int(row["width"]) / page_width,
                int(row["height"]) / page_height,
            ]
        blocks.append(block)
    output_text = "\n".join(" ".join(words) for words in lines.values())
    respond(
        {
            "ok": True,
            "output": {
                "language": requested_language,
                "text": output_text,
                "blocks": blocks if (request.get("input") or {}).get("return_blocks", True) else [],
                "meta": {"engine": "tesseract"},
            },
        }
    )


if __name__ == "__main__":
    main()
