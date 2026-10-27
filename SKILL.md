---
name: skill-router
description: 按能力调用本机已安装的 Skill（video.resolve、video.download、image.download、video.transcript），失败时自动换下一个实现。
---

# skill-router

不要直接挑某个平台脚本。向路由器要能力，由它发现、排序、调用、失败再换。

```bash
skillrouter list
skillrouter describe video.download
skillrouter invoke video.download --url "https://v.douyin.com/xxxx/" --save-to ./downloads --yes
skillrouter invoke video.transcript --url "https://v.douyin.com/xxxx/" --yes
skillrouter invoke image.download --input-file request.json --save-to ./downloads --yes
skillrouter doctor --report health.json --yes
skillrouter warmup example.faster-whisper
```

`--yes` 表示确认并信任将要执行的 Skill（会显示在 `NEED_CONFIRM` 里的 id、作者、side_effects、requires）。

个人使用，一次只处理一条用户给出的链接。不要批量抓取，不要把文件再上传或托管。

MCP 只暴露三个工具：`list_capabilities`、`describe_capability`、`invoke`。
