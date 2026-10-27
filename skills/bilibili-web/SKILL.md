# B 站 Web API Skill

用公开的 view / playurl 接口解析。先试 html5 的整段地址；不行再做 WBI 签名。DASH 的音视频分开时用 ffmpeg 合成，没有 ffmpeg 就不算下载成功。

单独调试：

```bash
python entry.py resolve <<'EOF'
{"skillcall":"0.1","capability":"video.resolve@1","input":{"url":"https://www.bilibili.com/video/BV1GJ411x7h7"},"context":{"output_dir":"/tmp/out","temp_dir":"/tmp/tmp","mode":"invoke"}}
EOF
```

机房 IP 经常收到 HTTP 412，这时返回 `GEO_BLOCKED`，路由器会换下一个 Skill，并且不把这次算作实现坏了。

可选凭证 `BILIBILI_COOKIES`（浏览器 cookie 串），需要在路由器里授权给 `example.bilibili-web` 才会注入。

个人使用，一次一条链接。
