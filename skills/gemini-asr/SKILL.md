# Gemini ASR

付费。把视频交给 `gemini-4-argon` 转写。只在免费 Skill 都失败、并且调用方允许 `allow_paid` 之后才会轮到它。

接口：

`POST https://generativelanguage.googleapis.com/v1beta/models/gemini-4-argon:generateContent`

请求头 `x-goog-api-key`。密钥只来自环境变量 `GEMINI_API_KEY`，不放进 URL，不写进 stdout / stderr。

`GEMINI_API_KEY` 以 `_API_KEY` 结尾，路由器默认不注入。需要同时：

1. 在 `credentials.json` 的 `grants` 里授权给 `example.gemini-asr`
2. 把 `GEMINI_API_KEY` 写进 `overrides`

视频来自本地 `file`，或通过路由器的 `video.download@1` 下载。不导入其他 Skill。

单测用 `GEMINI_API_BASE` 指向本机假服务器。正式调用不要设置这个变量。
