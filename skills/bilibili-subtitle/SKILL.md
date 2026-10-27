# 哔哩哔哩字幕

读播放器接口里的 CC / AI 字幕，不下载视频。优先走带 WBI 签名的播放器接口；无签名接口有时会返回空列表或别的视频的字幕。没有字幕轨时返回 `UNSUPPORTED_URL`，路由器会改试语音转写。机房 IP 返回 HTTP 412 时记为 `GEO_BLOCKED`。

下载用的体检视频 `BV1GJ411x7h7` 没有字幕轨。`BV16h411m7NG`（罗翔说刑法，2021）在家用网络上能打开，但播放器没有 CC / AI 字幕，会返回 `UNSUPPORTED_URL`，然后由 faster-whisper 转写。它不能当字幕体检样本。`BV1bnfdBpEPs` 是 `NOT_FOUND`（稿件不可见）。目前没有一条已核对的字幕 canary，体检保持关闭，未验证。

排序优先级高于本地 ASR，同一条哔哩哔哩链接会先走字幕。
