# yt-dlp Skill

用本机的 yt-dlp 解析和下载。未安装 yt-dlp 时返回 `DEPENDENCY_MISSING`（退出码 3），不把崩溃记成实现故障。

```bash
pip install yt-dlp
```

可选：把 Netscape cookie 文件路径放到路由器凭证 `YTDLP_COOKIES`，并授权给 `example.ytdlp`。小红书另认 `XHS_COOKIES`（Cookie 头或 Netscape 文件路径，同样要授权给 `example.ytdlp`）。短链被转到 `/login` 时返回 `NEED_AUTH`，不会把带 `xsec_token` 的地址写进错误信息。

覆盖的平台以 `skill.capability.yaml` 里的 `platforms` 为准。有 ffmpeg 时用 `bv*+ba/b` 合成 mp4，并把 ffmpeg 所在目录（解析 WinGet 这类符号链接之后）传给 yt-dlp。临时文件和成品都放在路由器给的输出目录里；成品优先于 `.part` 和分片文件。找不到 ffmpeg / ffprobe，或 Windows 报告找不到这个程序时，返回 `DEPENDENCY_MISSING`。合成失败但 ffmpeg 在，返回 `UPSTREAM_ERROR` 和 yt-dlp 的原文，而不是 `INTERNAL`。

个人使用，一次一条链接。
