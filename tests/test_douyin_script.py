from pathlib import Path

UPLOAD = Path("/home/ubuntu/.cursor/projects/workspace/uploads/douyin_dl_abf2.py")
COPY = Path(__file__).resolve().parents[1] / "skills" / "douyin-dl" / "douyin_dl.py"


def test_user_downloader_is_unmodified():
    assert COPY.is_file()
    text = COPY.read_text(encoding="utf-8")
    assert "def download(" in text
    assert "def parse_iesdouyin(" in text
    assert "def try_videofetch(" in text
    assert "def try_ytdlp(" in text
    if UPLOAD.is_file():
        assert COPY.read_bytes() == UPLOAD.read_bytes()
