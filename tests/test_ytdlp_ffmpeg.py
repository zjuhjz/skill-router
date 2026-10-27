import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _entry():
    path = REPO / "skills" / "ytdlp" / "entry.py"
    spec = importlib.util.spec_from_file_location("ytdlp_entry", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_login_redirect_is_need_auth_and_redacted():
    module = _entry()
    secret = "SuperSecretTokenValue99"
    wall = (
        "ERROR: Unsupported URL: https://www.xiaohongshu.com/login?redirectPath="
        "https%3A%2F%2Fwww.xiaohongshu.com%2Fdiscovery%2Fitem%2F649c46ab000000002702ad36"
        f"%3Ftype%3Dvideo%26xsec_token%3D{secret}%26share_id%3DShareIdValue88"
        f"&xsec_token={secret}&shareRedId=RedIdValue77&a_bogus=BogusValue66"
    )
    assert module._classify(wall, ffmpeg_missing=False) == "NEED_AUTH"
    assert secret not in module.redact_text(wall)
    assert "ShareIdValue88" not in module.redact_text(wall)
    import contextlib
    import io
    import json

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            module._fail_extract(wall, ffmpeg_missing=False)
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("expected SystemExit")
    payload = json.loads(buf.getvalue())
    assert payload["error"]["code"] == "NEED_AUTH"
    assert "XHS_COOKIES" in payload["error"]["message"]
    assert secret not in buf.getvalue()


def test_xhs_cookie_header_becomes_netscape_file(tmp_path):
    module = _entry()
    path = module._as_netscape("Cookie: a1=one; web_session=two", tmp_path / "xhs-cookies.txt")
    text = Path(path).read_text(encoding="utf-8")
    assert "web_session\ttwo" in text
    assert text.startswith("# Netscape HTTP Cookie File")
    again = module._as_netscape(path, tmp_path / "unused.txt")
    assert again == path


def test_missing_ffmpeg_is_dependency_missing_not_internal():
    module = _entry()
    samples = [
        "ERROR: ffprobe and ffmpeg not found. Please install or provide the path using --ffmpeg-location",
        "ERROR: Postprocessing: ffmpeg not installed",
        "WARNING: You have requested merging of multiple formats but ffmpeg is not installed",
        "ERROR: [Merger] ffmpeg exited",
    ]
    for text in samples:
        assert module._classify(text, ffmpeg_missing=True) == "DEPENDENCY_MISSING"
    assert module._classify("Requested format is not available", ffmpeg_missing=True) == "DEPENDENCY_MISSING"
    assert module._classify("Fresh cookies are needed", ffmpeg_missing=False) == "NEED_AUTH"
    assert module._classify("[WinError 2] The system cannot find the file specified ffmpeg", ffmpeg_missing=False) == "DEPENDENCY_MISSING"
    assert module._classify("ERROR: Postprocessing: ffmpeg exited with code 1", ffmpeg_missing=False) == "UPSTREAM_ERROR"
    assert "ffmpeg is not installed" in module._FFMPEG_HINT


def test_windows_prefix_and_merged_file_stay_inside(tmp_path):
    module = _entry()
    assert module._path_key(__import__("pathlib").Path(r"\\?\C:\Users\a\out\clip.mp4")) == module._path_key(
        __import__("pathlib").Path(r"C:\Users\a\out\clip.mp4")
    )
    out = tmp_path / "out"
    out.mkdir()
    (out / "clip.f30080.mp4").write_bytes(b"frag")
    (out / "clip.mp4.part").write_bytes(b"part-file")
    merged = out / "clip.mp4"
    merged.write_bytes(b"ftyp-merged")
    chosen = module._place(tmp_path / "elsewhere" / "nope.mp4", out)
    assert chosen == merged
    assert module._relative_output(chosen, out) == "clip.mp4"
