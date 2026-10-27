# 小红书笔记页

打开用户给出的笔记链接，解析页面里的 `window.__INITIAL_STATE__`。长链是 `xiaohongshu.com/explore/<id>` 或 `xiaohongshu.com/discovery/item/<id>`。短链域名是 `xhslink.com` 和 `xhslink.cn`，路径有 `/a`、`/m`、`/o`。分享文案里可以只有这一段短链，前后是「复制后直接打开【小红书】」这类字。短链会跟着跳转，并保留查询串里的 `xsec_token`。

- 视频笔记提供 `video.resolve@1` 和 `video.download@1`。排序优先级高于 `example.ytdlp`，所以先走这一份；失败后路由器再换 yt-dlp。
- 图文笔记提供 `image.download@1`。链接上带 `type=video`，或笔记对象的 `type` 是 `video` 时，返回 `UNSUPPORTED_URL`（`this is a video note; use video.download`）。封面图不算图文笔记。
- 公开笔记页需要分享链接上的 `xsec_token`。没有这个参数、页面又不是笔记时，返回 `UPSTREAM_CHANGED`。registry 里的 canary 没有 token，`canary_verified: false`，不要拿它当成功样本，也不要把用户刚复制的短链写进仓库。
- 最终路径是 `/login`、`/captcha` 或 `/404/sec_` 时返回 `NEED_AUTH`。页头和脚本里的「登录」「captcha」不算。页面写明地区限制才返回 `GEO_BLOCKED`。
- 笔记没解析出来时，用同一个 `xsec_token` 再请求 `/explore/<id>` 和 `/discovery/item/<id>`。
- `XHS_COOKIES` 可以是浏览器里的 Cookie 头，或 Netscape `cookies.txt` 的路径。要在 `grants` 里授权给 `example.xiaohongshu-web`。Cookie 放在 jar 里，短链跳转后的页面也会带上。导出步骤见仓库 README。`SKILLROUTER_DEBUG=1` 把每一跳的路径和 Cookie 长度写到 `debug.jsonl`。
- 请求头按普通文档导航来发（`Accept`、`Sec-Fetch-Mode: navigate`），和 yt-dlp 一样，不先打开首页。首页经常 302 到 `/login`，再请求笔记就会像没登录。匿名的 `login/activate` 没有 `X-S` 签名时返回 406，拿不到 `web_session`，所以这里不靠它。
- yt-dlp 在标题为空时会写成 `XiaoHongShu video #<id>`。那是它的占位标题，不是笔记标题。
