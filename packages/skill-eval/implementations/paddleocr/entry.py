#!/usr/bin/env python3
from __future__ import annotations

import json
import inspect
import struct
import sys
from collections.abc import Mapping
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


def png_size(path: Path) -> tuple[int, int] | None:
    header = path.read_bytes()[:24]
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return struct.unpack(">II", header[16:24])
    return None


def old_api_blocks(result: object, size: tuple[int, int] | None) -> list[dict]:
    rows = result[0] if isinstance(result, list) and result and isinstance(result[0], list) else result
    blocks = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        polygon, recognition = item[0], item[1]
        if not isinstance(recognition, (list, tuple)) or not recognition:
            continue
        block = {"text": str(recognition[0]), "confidence": float(recognition[1])}
        if size and isinstance(polygon, (list, tuple)) and polygon:
            xs = [float(point[0]) for point in polygon]
            ys = [float(point[1]) for point in polygon]
            width, height = size
            block["bbox"] = [min(xs) / width, min(ys) / height, (max(xs) - min(xs)) / width, (max(ys) - min(ys)) / height]
        blocks.append(block)
    return blocks


def normalized_bbox(polygon: object, size: tuple[int, int] | None) -> list[float] | None:
    if not size or not isinstance(polygon, (list, tuple)) or not polygon:
        return None
    if len(polygon) == 4 and all(isinstance(value, (int, float)) for value in polygon):
        left, top, right, bottom = (float(value) for value in polygon)
    else:
        try:
            xs = [float(point[0]) for point in polygon]
            ys = [float(point[1]) for point in polygon]
        except (IndexError, TypeError, ValueError):
            return None
        left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
    width, height = size
    return [left / width, top / height, (right - left) / width, (bottom - top) / height]


def new_api_blocks(result: object, size: tuple[int, int] | None) -> list[dict]:
    blocks = []
    items = result if isinstance(result, list) else list(result) if result is not None else []
    for item in items:
        payload = getattr(item, "json", item)
        if callable(payload):
            payload = payload()
        if not isinstance(payload, Mapping):
            continue
        data = payload.get("res", payload)
        if not isinstance(data, Mapping):
            continue
        texts = data.get("rec_texts") or []
        scores = data.get("rec_scores") or []
        polygons = data.get("rec_polys") or data.get("rec_boxes") or []
        for index, text in enumerate(texts):
            block = {"text": str(text)}
            if index < len(scores):
                block["confidence"] = float(scores[index])
            if index < len(polygons):
                bbox = normalized_bbox(polygons[index], size)
                if bbox is not None:
                    block["bbox"] = bbox
            blocks.append(block)
    return blocks


def recognize(PaddleOCR: type, image: Path, language: str) -> tuple[list[dict], str]:
    paddle_language = "ch" if language.lower().startswith("zh") else "en"
    parameters = inspect.signature(PaddleOCR).parameters
    if "use_doc_orientation_classify" in parameters:
        engine = PaddleOCR(
            lang=paddle_language,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
        )
        result = list(engine.predict(str(image)))
        return new_api_blocks(result, png_size(image)), "3.x"
    engine = PaddleOCR(lang=paddle_language, use_angle_cls=True, show_log=False)
    result = engine.ocr(str(image), cls=True)
    return old_api_blocks(result, png_size(image)), "2.x"


def main() -> None:
    try:
        request = json.load(sys.stdin)
    except (json.JSONDecodeError, UnicodeDecodeError):
        fail("INVALID_INPUT", "stdin must contain one JSON request")
    try:
        from paddleocr import PaddleOCR
    except ImportError as exc:
        fail("DEPENDENCY_MISSING", f"install paddleocr and paddlepaddle: {exc}", 3)
    image = resolve_file(request)
    language = str((request.get("input") or {}).get("language") or "en")
    try:
        blocks, api_version = recognize(PaddleOCR, image, language)
    except Exception as exc:
        fail("INTERNAL", f"PaddleOCR failed: {type(exc).__name__}: {exc}")
    if not blocks:
        fail("INTERNAL", "PaddleOCR returned an unsupported or empty result")
    respond(
        {
            "ok": True,
            "output": {
                "language": language,
                "text": "\n".join(block["text"] for block in blocks),
                "blocks": blocks if (request.get("input") or {}).get("return_blocks", True) else [],
                "meta": {"engine": "paddleocr", "api": api_version},
            },
        }
    )


if __name__ == "__main__":
    main()
