# 本地 faster-whisper

免费的语音转写。CPU 可以跑，默认模型 `small`、`int8`。中文建议在输入里带 `"language": "zh"`。

视频从两种地方来：

- `input.file`：本地文件。相对路径相对路由器进程的工作目录。
- `input.url`：调用路由器的 `video.download@1`（`python -m skillrouter invoke`），不导入其他 Skill。

没装 faster-whisper 时返回 `DEPENDENCY_MISSING`（退出码 3）：

```bash
pip install "skill-router[asr]"
```

可选环境变量：`WHISPER_MODEL`（默认 `small`）、`WHISPER_DEVICE`（默认 `cpu`）、`WHISPER_COMPUTE_TYPE`（默认 `int8`）、`WHISPER_VAD`（默认关闭）、`HF_ENDPOINT`。短视频里的歌声如果打开 VAD，会被裁成一段听不清的字。`language` 以 `zh` 开头时，默认提示要求简体中文，并用 `zhconv` 把结果收成简体。输出里的 `script` 为 `Hans`（`zh-TW` / `zh-HK` / `zh-Hant` 则为 `Hant`）。

模型下载和这一次转写分开。缓存里没有模型时，先单独下载：`huggingface.co` 若 15 秒没有进度，或前 12 秒不到约 24MB，就改走 `https://hf-mirror.com`（已经设置了别的 `HF_ENDPOINT` 则尊重它）。失败返回 `DEPENDENCY_MISSING`，并提示设置 `HF_ENDPOINT=https://hf-mirror.com`。调用若在下载过程中被路由器掐掉，也按这个码返回，而不是光秃秃的 `TIMEOUT`。

可以事先把模型下好，不受 `invoke` 的 300 秒总时限约束：

```bash
skillrouter warmup example.faster-whisper
skillrouter doctor --warmup --yes
```

`faster-whisper` 1.2.1 仍会把 `metadata_errors` 传给 `av.open`，而 PyAV 19 已去掉这个参数。安装 extra 会把 `av` 限制在 19 以下，并带上 `zhconv`。

体检默认关闭，避免 doctor 每次都下载模型。`warmup` 才是下载步骤。
