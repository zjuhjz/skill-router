# 抖音 iesdouyin Skill

独立于 `douyin_dl.py` 的第二份实现，声明同一个 `video.resolve@1` / `video.download@1`。路由器会在前一个失败后自动换到这一个。

单独运行（不经过路由器）时，这个目录没有自己的分享链接 CLI；请通过 skillcall 的 stdin JSON 调用 `entry.py`，或直接用路由器：

```bash
skillrouter invoke video.resolve --input '{"url":"https://v.douyin.com/xxxx/"}' --yes
```

个人使用，一次一条链接。
