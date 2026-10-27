import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load(folder: str, name: str):
    path = REPO / "skills" / folder / "entry.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_kuaishou_share_state_finds_photo():
    module = _load("kuaishou-web", "kuaishou_entry")
    state = {
        "wrapped": {
            "photo": {
                "photoId": "5217",
                "caption": "标题",
                "userName": "作者",
                "photoType": "VIDEO",
                "duration": 35166,
                "mainMvUrls": [{"url": "https://cdn.example/a.mp4"}],
                "manifest": {
                    "adaptationSet": [
                        {"representation": [{"url": "https://cdn.example/hi.mp4", "height": 1280, "width": 720}]}
                    ]
                },
            }
        }
    }
    photo = module.find_photo(state)
    assert photo["caption"] == "标题"
    media = module._media(photo)
    assert media[0]["url"].endswith("hi.mp4")
    assert media[0]["height"] == 1280
    meta = module._meta(photo, "3xabc", "https://www.kuaishou.com/short-video/3xabc")
    assert meta["platform"] == "kuaishou"
    assert meta["duration_ms"] == 35166


def test_xiaohongshu_state_and_security_page(tmp_path):
    module = _load("xiaohongshu-web", "xhs_entry")
    html = """
    <script>window.__INITIAL_STATE__={"note":{"noteDetailMap":{"abc":{"note":{
      "noteId":"649c46ab000000002702ad36","type":"normal","title":"图","desc":"文",
      "user":{"nickname":"甲","userId":"u1"},
      "imageList":[{"urlDefault":"https://cdn.example/0.jpg","width":1080,"height":1440}],
      "video":{"media":{"stream":{"h264":[{"masterUrl":"https://cdn.example/v.mp4","height":1280,"width":720}]}}}
    }}}}}</script>
    """
    note = module.find_note(module.extract_initial_state(html))
    assert note["title"] == "图"
    assert module._images(note)[0]["url"].endswith("0.jpg")
    assert module._videos(note)[0]["height"] == 1280
    blocked = "https://www.xiaohongshu.com/404?source=/404/sec_abc"
    assert module.classify_page(blocked, "<html>") == "NEED_AUTH"
    code, message = module.page_failure("https://www.xiaohongshu.com/explore/649c46ab000000002702ad36", blocked, "<html>")
    assert code == "NEED_AUTH"
    assert "XHS_COOKIES" in message
    share = "https://www.xiaohongshu.com/explore/649c46ab000000002702ad36?xsec_token=ABcd_ef-12&xsec_source=app_share"
    assert module.share_token(share) == "ABcd_ef-12"
    code, message = module.page_failure(share, blocked, "<html>登录</html>")
    assert code == "NEED_AUTH"
    assert "datacenter" not in message.lower()
    region = module.page_failure(share, share, "<html>该内容在当前地区不可用</html>")
    assert region[0] == "GEO_BLOCKED"
    plain = module.page_failure(share, share, "<html>登录</html>")
    assert plain is None
    undefined = html.replace('"图"', "undefined")
    assert module.extract_initial_state(undefined) is not None
    discovery = (
        "https://www.xiaohongshu.com/discovery/item/649c46ab000000002702ad36"
        "?type=video&xsec_token=ABcd_ef-12&xsec_source=app_share"
    )
    page = """
    <html>登录<button>登录</button>
    <script>window.__INITIAL_STATE__={"note":{"noteDetailMap":{"649c46ab000000002702ad36":{"note":{
      "noteId":"649c46ab000000002702ad36","type":"video","title":"","desc":"真实说明",
      "user":{"nickname":"甲","userId":"u1"},
      "imageList":[{"urlDefault":"https://cdn.example/cover.jpg","width":720,"height":1280}],
      "video":{"media":{"stream":{"h264":[{"masterUrl":"https://cdn.example/v.mp4","height":1280,"width":720}]}}}
    }}}}}</script></html>
    """
    loose = page.replace("}}}}</script>", ",}}}}</script>")
    state = module.extract_initial_state(loose)
    note = module.note_from_state(state, discovery)
    assert note["noteId"] == "649c46ab000000002702ad36"
    meta = module._meta(note, discovery)
    assert meta["title"] == "真实说明"
    assert meta["author"] == "甲"
    assert meta["cover"].endswith("cover.jpg")
    assert meta["page_url"] == "https://www.xiaohongshu.com/explore/649c46ab000000002702ad36"
    assert "xsec_token" not in meta["page_url"]
    assert module._videos(note)[0]["url"].endswith("v.mp4")
    code, message = module.failure_for("images", discovery, discovery, page, note)
    assert code == "UNSUPPORTED_URL"
    assert "video note" in message
    assert module.failure_for("download", discovery, discovery, page, note) is None
    login = (
        "https://www.xiaohongshu.com/login?redirectPath="
        "https%3A%2F%2Fwww.xiaohongshu.com%2Fdiscovery%2Fitem%2F649c46ab000000002702ad36%3Ftype%3Dvideo"
    )
    code, message = module.failure_for("images", discovery, login, "<html>请登录</html>", None)
    assert code == "UNSUPPORTED_URL"
    assert "video note" in message
    code, message = module.failure_for(
        "download",
        "https://www.xiaohongshu.com/explore/649c46ab000000002702ad36?xsec_token=ABcd_ef-12",
        login,
        "<html>登录</html>",
        None,
    )
    assert code == "NEED_AUTH"
    logged_in = "<html>登录 login<button>登录</button><script>function captchaInit(){return 'captcha'}</script></html>"
    assert module.page_failure(discovery, discovery, logged_in) is None
    code, _message = module.failure_for("download", discovery, discovery, logged_in, None)
    assert code == "UPSTREAM_CHANGED"
    pages = module.alternate_pages(
        "https://www.xiaohongshu.com/login?redirectPath="
        "https%3A%2F%2Fwww.xiaohongshu.com%2Fdiscovery%2Fitem%2F649c46ab000000002702ad36"
        "%3Fxsec_token%3DABcd_ef-12%26xsec_source%3Dapp_share",
        "https://www.xiaohongshu.com/login",
        "",
    )
    assert pages[0].startswith("https://www.xiaohongshu.com/explore/649c46ab000000002702ad36?xsec_token=")
    assert "/discovery/item/649c46ab000000002702ad36" in pages[1]
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text(
        "# Netscape HTTP Cookie File\n.xiaohongshu.com\tTRUE\t/\tFALSE\t0\ta1\tone\n",
        encoding="utf-8",
    )
    assert module.cookie_header(str(cookie_file)) == "a1=one"
    assert module.cookie_header("Cookie: web_session=two; a1=one") == "web_session=two; a1=one"
    assert module._known_host("https://xhslink.cn/o/ExampleShare")
    retry = module._with_token(
        "https://www.xiaohongshu.com/discovery/item/649c46ab000000002702ad36",
        "xsec_token=ABcd_ef-12",
    )
    assert "/explore/649c46ab000000002702ad36?xsec_token=" in retry


def test_cookie_survives_redirect_and_debug_is_redacted(tmp_path, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    module = _load("xiaohongshu-web", "xhs_cookie_redirect")
    seen: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            seen.append((path, self.headers.get("Cookie")))
            if path == "/s":
                self.send_response(302)
                location = f"http://127.0.0.1:{self.server.server_address[1]}/discovery/item/abc"
                self.send_header("Location", location)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html>ok</html>")

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("XHS_COOKIES", "a1=one; web_session=two")
        session = module._session(domains=("127.0.0.1",))
        session.get(f"http://127.0.0.1:{port}/s", timeout=5)
    finally:
        server.shutdown()
    assert [item[0] for item in seen] == ["/s", "/discovery/item/abc"]
    assert seen[0][1] and "web_session=two" in seen[0][1]
    assert seen[1][1] and "web_session=two" in seen[1][1]
    monkeypatch.setenv("SKILLROUTER_DEBUG", "1")
    monkeypatch.setenv("SKILLROUTER_HOME", str(tmp_path))
    module.write_debug(
        {
            "detector": "login_path",
            "cookie_sent": True,
            "cookie_length": len("a1=one; web_session=two"),
            "hops": [
                {
                    "host": "www.xiaohongshu.com",
                    "path": "/login?redirectPath=https://www.xiaohongshu.com/explore/abc?xsec_token=SECRETTOKENVALUE",
                    "status": 200,
                    "cookie_sent": True,
                    "cookie_length": 24,
                }
            ],
        }
    )
    text = (tmp_path / "logs" / "debug.jsonl").read_text(encoding="utf-8")
    assert "SECRETTOKENVALUE" not in text
    assert "web_session" not in text
    assert "login_path" in text
    assert '"path": "/login"' in text
    assert "cookie_length" in text


def test_logged_in_note_shapes_meta_fallback_and_skill_debug(tmp_path, monkeypatch):
    module = _load("xiaohongshu-web", "xhs_shapes")
    note_id = "69ce2b520000000023010e52"
    other_id = "111111111111111111111111"
    discovery = (
        f"https://www.xiaohongshu.com/discovery/item/{other_id}"
        "?type=video&xsec_token=ABcd_ef-12&xsec_source=app_share"
    )
    html = f"""
    <html>
    <meta property="og:title" content="夜跑 - 小红书" />
    <meta name="description" content="描述正文" />
    <meta property="og:image" content="https://cdn.example/og.jpg" />
    <meta name="author" content="作者名" />
    <script>window.__INITIAL_STATE__={{
      "note": {{
        "firstNoteId": "{note_id}",
        "noteDetailMap": {{
          "{other_id}": {{"note": undefined}},
          "{note_id}": {{"note": {{
            "noteId": "{note_id}",
            "type": "video",
            "title": "夜景",
            "desc": "说明文字",
            "user": {{"nickname": "乙", "userId": "u2"}},
            "imageList": [{{"urlDefault": "https://cdn.example/cover.jpg"}}],
            "video": {{"media": {{"stream": {{"h264": [{{"masterUrl": undefined, "backupUrls": ["https://cdn.example/backup.mp4"]}}]}}}}}}
          }}}}
        }}
      }},
    }}</script>
    </html>
    """
    note = module._read_note(html, discovery)
    assert note["noteId"] == note_id
    assert note["title"] == "夜景"
    assert module._videos(note)[0]["url"].endswith("backup.mp4")
    meta = module._meta(note, discovery)
    assert meta["author"] == "乙"
    assert meta["cover"].endswith("cover.jpg")
    assert meta["page_url"] == f"https://www.xiaohongshu.com/explore/{note_id}"
    outline = json.dumps(module.state_outline(module.extract_initial_state(html)), ensure_ascii=False)
    assert "夜景" not in outline
    assert "说明文字" not in outline
    assert "noteDetailMap" in outline
    assert "firstNoteId" in outline

    thin = html.replace('"title": "夜景"', '"title": ""').replace('"nickname": "乙"', '"nickname": ""')
    filled = module._read_note(thin, discovery)
    assert filled["title"] == "夜跑"
    assert module._author(filled)[0] == "作者名"

    card = f"""
    <script>window.__INITIAL_STATE__={{"note":{{"firstNoteId":"{note_id}","noteDetailMap":{{
      "{note_id}": {{"note": null, "noteCard": {{
        "noteId":"{note_id}","type":"normal","title":"卡片","desc":"图文",
        "user":{{"nickname":"丙"}},
        "imageList":[{{"urlDefault":"https://cdn.example/card.jpg"}}]
      }}}}
    }}}}}}</script>
    """
    card_note = module._read_note(card, f"https://www.xiaohongshu.com/explore/{note_id}")
    assert card_note["title"] == "卡片"
    assert module._videos(card_note) == []

    mobile = f"""
    <script>window.__INITIAL_STATE__={{"noteData":{{"data":{{"noteData":{{
      "noteId":"{note_id}","type":"video","title":"移动页","desc":"短",
      "user":{{"nickname":"丁"}},
      "imageList":[{{"urlDefault":"https://cdn.example/m.jpg"}}],
      "video":{{"consumer":{{"originVideoKey":"stream/origin.mp4"}}}}
    }}}}}}}}</script>
    """
    mobile_note = module._read_note(mobile, f"https://www.xiaohongshu.com/explore/{note_id}")
    assert mobile_note["title"] == "移动页"
    assert module._videos(mobile_note)[0]["url"] == "https://sns-video-bd.xhscdn.com/stream/origin.mp4"

    image_html = f"""
    <meta property="og:title" content="封面 - 小红书" />
    <meta property="og:image" content="https://cdn.example/pic.jpg" />
    <script>var ad = "https://sns-video.xhscdn.com/ad.mp4";
    window.__INITIAL_STATE__={{"user":{{"loggedIn":true,"nickname":"不应出现"}}}}</script>
    """
    image_note = module._read_note(image_html, f"https://www.xiaohongshu.com/explore/{note_id}")
    assert image_note["type"] == "normal"
    assert image_note["title"] == "封面"
    assert module._videos(image_note) == []
    assert module._images(image_note)[0]["url"].endswith("pic.jpg")

    og_html = f"""
    <meta property="og:title" content="夜跑 - 小红书" />
    <meta name="description" content="描述正文" />
    <meta property="og:image" content="https://cdn.example/og.jpg" />
    <meta name="author" content="作者名" />
    <script>window.__INITIAL_STATE__={{"user":{{"loggedIn":true}}}}
    "originVideoKey":"pre_post/clip.mp4"</script>
    """
    og_note = module._read_note(og_html, discovery.replace(other_id, note_id))
    assert og_note["title"] == "夜跑"
    assert module._author(og_note)[0] == "作者名"
    assert module._images(og_note)[0]["url"].endswith("og.jpg")
    assert module._videos(og_note)[0]["url"].endswith("pre_post/clip.mp4")

    module._HOPS = [
        {
            "host": "www.xiaohongshu.com",
            "path": f"/discovery/item/{note_id}?xsec_token=SECRETTOKENVALUE&xsec_source=app_share",
            "status": 200,
            "cookie_sent": True,
            "cookie_length": 28,
        }
    ]
    monkeypatch.delenv("SKILLROUTER_DEBUG", raising=False)
    monkeypatch.setenv("XHS_COOKIES", "web_session=UniqueCookieValue99")
    module.arm_debug({"debug": True, "debug_log": str(tmp_path / "logs" / "debug.jsonl")})
    module.write_debug(module._debug_record(html, "note"))
    text = (tmp_path / "logs" / "debug.jsonl").read_text(encoding="utf-8")
    for leaked in ("SECRETTOKENVALUE", "UniqueCookieValue99", "web_session", "夜景", "说明文字", "描述正文"):
        assert leaked not in text
    assert '"path": "/discovery/item/' + note_id + '"' in text
    assert '"detector": "note"' in text
    assert "noteDetailMap" in text
    assert "og:title" in text
    assert "cookie_length" in text


def test_weibo_status_video_and_images():
    module = _load("weibo-web", "weibo_entry")
    status = {
        "id": "5052484659707960",
        "mid": "5052484659707960",
        "text": "你好<br />世界",
        "user": {"id": 1, "screen_name": "甲"},
        "page_info": {
            "type": "video",
            "media_info": {"stream_url": "http://cdn.example/a.mp4", "stream_url_hd": "//cdn.example/hd.mp4", "duration": 12},
            "urls": {"mp4_720p_mp4": "//cdn.example/720.mp4"},
        },
        "pics": [{"large": {"url": "https://cdn.example/p.jpg", "width": 800, "height": 600}}],
    }
    videos = module.video_from_status(status)
    assert videos[0]["url"] == "https://cdn.example/720.mp4"
    images = module.images_from_status(status)
    assert images[0]["url"].endswith("p.jpg")
    assert module._plain(status["text"]) == "你好 世界"
    component = {"urls": {"高清 1080P": "//f.video.weibocdn.com/a.mp4", "标清 480P": "//f.video.weibocdn.com/b.mp4"}}
    ordered = module.video_from_component(component)
    assert "1080" not in ordered[0]["url"]
    assert ordered[0]["url"].endswith("/a.mp4")
