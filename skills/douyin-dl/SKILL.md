# Douyin (抖音) headless download — preferred pipeline

Reusable script in this folder: `douyin_dl.py`. The router calls `skillcall_entry.py`; you can still run the script yourself.

```bash
python douyin_dl.py 'https://v.douyin.com/xxxx/' -o ./out
python douyin_dl.py URL --json
```

## Priority order

1. **Self-parse iesdouyin (default, fastest, no third party)**
2. **videofetch** (KuKuTool wrapper) — only if installed and step 1 fails
   Note: KuKuTool’s public `/api/parse` often returns a security dialog for headless IPs; prefer step 1.
3. **yt-dlp** — only with `--allow-ytdlp` (often needs fresh cookies; last resort)

The skillcall adapter uses step 1 only. Fallback to another Skill is the router's job. The script's own CLI is unchanged.

## How step 1 works (2026-10)

1. Follow `v.douyin.com` redirects → numeric aweme / video id.
2. Warm-up `GET https://www.iesdouyin.com/share/video/{id}/` with an **iPhone Safari** User-Agent to obtain `ttwid` cookie.
   Without this cookie, `?is_v_html=true` often returns a slim `_ROUTER_DATA` with **no** `videoInfoRes`.
3. `GET .../share/video/{id}/?is_v_html=true` (same UA + cookie).
4. Parse `window._ROUTER_DATA` → `loaderData["video_(id)/page"].videoInfoRes.item_list[0]`.
5. Read `video.play_addr.uri` and download:
   `https://www.iesdouyin.com/aweme/v1/play/?video_id={uri}&ratio=720p&line=0`
   Fallback: take `play_addr.url_list` and replace `playwm` → `play`.

## Output

- `--json` fields: `path`, `size`, `duration_ms`, `desc`, `author`, `method`, `parse_seconds`, `download_seconds`

## Personal-use only

Respect copyright and the platform terms. One link at a time, for your own reference. Do not redistribute.
