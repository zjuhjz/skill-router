# skill-router

**skill-router** 是统一的 Skill 调用库，协议名 **skillcall**，这个仓库是它的参考实现。它给 AI 助手做通用的 Skill 分发：助手不绑定某一个平台脚本，只按能力名调用。中文短视频平台（抖音、哔哩哔哩、快手、小红书、微博）是第一批用例。

AI 向路由器要一个能力（例如 `video.download@1`），路由器找出声明了这个能力的 Skill，按成本、覆盖面、健康和熔断排序，用 stdin/stdout JSON 调用其中一个。失败时按错误码决定要不要换下一个。

路由器代码里没有平台名字。平台 ID、URL 形态、公共体检链接、能力 schema 和错误码都在 [`registry/`](registry/)。示例实现在 [`skills/`](skills/)，每个目录都能单独跑。协议说明见 [SPEC.md](SPEC.md)（v0.3）。

0.3.8 的能力：`video.resolve@1`、`video.download@1`、`image.download@1`、`video.transcript@1`。

### 排序

免费 Skill 排在付费和按量计费前面。付费默认不调用，除非 `allow_paid` 或 `--allow-paid`。

免费候选里，只声明当前平台的 Skill 优先于声明了多个平台或通配符（`*`）的 Skill。健康分未知时也是这样。只有专用 Skill 被停用，或样本不少于 3 且健康分低于 50，才让通用 Skill 先试。

`video.resolve` 在其余条件打平时，用 sidecar（`skill.capability.yaml`）的 `metadata: rich|basic` 打破平局：`rich` 先于 `basic`。下载和其他能力不看这个字段。

因此默认顺序是：

- 抖音：`douyin-dl` → `douyin-ies` → `yt-dlp`
- 哔哩哔哩：`bilibili-web` 在 `yt-dlp` 前面

## 安装

需要 Python 3.10+（在 Windows 上用 3.12）。

```bash
pip install -e ".[skills,dev]"
```

`skills` 额外依赖是示例 Skill 用的：`requests` 和 `yt-dlp`。路由器本身不导入它们。本地转写再加：

```bash
pip install "skill-router[asr]"
```

这个 extra 安装 `faster-whisper`、`zhconv`，并把 `av` 限制在 19 以下。`faster-whisper` 1.2.1 还会传 `metadata_errors`，PyAV 19 已经去掉该参数，装到 19 时转写会在打开文件时失败。中文结果默认收成简体，输出里带 `script`（`Hans` 或 `Hant`）。

第一次转写要下载模型。`huggingface.co` 卡住或太慢时会改试 `https://hf-mirror.com`；也可以自己设 `HF_ENDPOINT`。下载失败，或调用在下载过程中超时，返回 `DEPENDENCY_MISSING`，并提示镜像地址。模型下载不占用这一次转写的成功与否：先运行 `skillrouter warmup example.faster-whisper`（或 `skillrouter doctor --warmup`），它不受 `invoke` 默认 300 秒总时限约束。

B 站 DASH 和 yt-dlp 合流需要系统里有 `ffmpeg`。没有的话，声明了 `binaries: [ffmpeg]` 的下载能力会被标成未就绪；yt-dlp 在必须合流又找不到 ffmpeg 时返回 `DEPENDENCY_MISSING`，并在 `hints` 里说明，而不是 `INTERNAL`。解析不依赖 ffmpeg。

示例 Skill 放在本仓库的 `skills/`。在仓库根目录运行时，路由器会发现它们。也可以把某个 Skill 目录复制到：

- `~/.skillrouter/skills/`
- `~/.claude/skills/`
- `~/.cursor/skills/`
- 项目里的 `.skills/`

没有 `skill.capability.yaml` 的文件夹会被忽略，Agent 仍可按它自己的 `SKILL.md` 单独使用。

## 命令

```bash
skillrouter list
skillrouter describe video.download
skillrouter invoke video.resolve --url "https://v.douyin.com/xxxx/" --yes
skillrouter invoke video.download --url "https://v.douyin.com/xxxx/" --save-to ./downloads --yes
skillrouter invoke video.download --input-file request.json --save-to ./downloads --yes
skillrouter invoke video.transcript --url "https://v.douyin.com/xxxx/" --yes
skillrouter invoke image.download --url "https://www.xiaohongshu.com/explore/xxxx" --save-to ./downloads --yes
skillrouter doctor --report health.json --yes
skillrouter warmup example.faster-whisper
skillrouter restore <skill_id>
skillrouter restore <skill_id> <capability> <platform>
```

`--input`、`--input-file`、`--url` 三者给其中一个。PowerShell 5 里给 `--input` 传 JSON 要转义引号，用 `--url` 或 `--input-file` 即可。`--input-file` 按 UTF-8 读取，可以带 BOM。

第一次调用新安装或升级过的 Skill 会返回 `NEED_CONFIRM`，并带上 id、作者、`side_effects` 和 `requires`。确认后加 `--yes`，信任会按内容哈希记下来。内容一变就要再确认。

`invoke` 的 JSON 和 MCP 工具 `invoke` 相同：`ok`、`output`、`served_by`、`attempts`、`skipped`、`hints`。

## MCP

只暴露三个工具，和装了多少 Skill 无关：

| 工具 | 作用 |
|---|---|
| `list_capabilities` | 有哪些能力、哪些平台、健康情况和要什么凭证 |
| `describe_capability` | 输入 schema、说明和示例 |
| `invoke` | 调用。`options` 可带 `prefer_skill`、`allow_paid`、`max_attempts`、`save_to`、`yes` |

Cursor（`.cursor/mcp.json`）和 Claude Desktop（`claude_desktop_config.json`）可以用同一段。如果 `skillrouter` 不在 PATH 上，把 `command` 换成 `python`，`args` 换成 `["-m", "skillrouter", "mcp"]`。

```json
{
  "mcpServers": {
    "skill-router": {
      "command": "skillrouter",
      "args": ["mcp"],
      "env": {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "SKILLROUTER_YES": "1"
      }
    }
  }
}
```

`SKILLROUTER_YES=1` 适合你自己的机器：跳过每次确认。不设的话，新 Skill 会先返回 `NEED_CONFIRM`。完整示例在 [`examples/cursor-mcp.json`](examples/cursor-mcp.json) 和 [`examples/claude-desktop-mcp.json`](examples/claude-desktop-mcp.json)。

## 凭证

Skill 在声明里写出变量名，不等于它能读到这个变量。值放在 `~/.skillrouter/credentials.json`（或 `SKILLROUTER_CRED_<NAME>`），并且 `grants` 里明确授权给那个 Skill id 之后才会注入子进程。父进程里的同名变量不会透传。

`AWS_*`、`GITHUB_*`、`SSH_*`、`OPENAI_*`、`ANTHROPIC_*`、`AZURE_*`，以及以 `_API_KEY` 结尾的名字，默认一律不注入。要放行某个名字，把它写进 `overrides`。

示例见 [`examples/credentials.json`](examples/credentials.json)。错误信息、调用日志和健康记录里的 `xsec_token`、`xsec_source`、`share_id`、`shareRedId`、`a_bogus`、`msToken`、`cookies`，以及名字里带 `token` 或 `sign` 的查询参数，都会换成 `***`。百分号编码（`%3D`、`%26`）里的同一类参数也会抹掉。

### 小红书登录 Cookie（Windows）

家用网络如果被转到登录页或安全校验页，`example.xiaohongshu-web` 和 `example.ytdlp` 都会返回 `NEED_AUTH`。把你自己浏览器里已经登录的小红书 Cookie 交给这两个 Skill。这是你的登录态，不要写进仓库，也不要贴到聊天里。

1. 用 Chrome 或 Edge 打开 [https://www.xiaohongshu.com](https://www.xiaohongshu.com)，登录你自己的账号，再打开一篇你能看的笔记。
2. 按 F12，打开「网络 / Network」，勾选「保留日志 / Preserve log」，刷新页面。
3. 在列表里点选类型为 `document` 的那条笔记请求。右侧「标头 / Headers」→「请求标头 / Request Headers」里找到 `Cookie`。
4. 复制 `Cookie` 后面的整段值（`a1=...; web_session=...; webId=...`）。不要只复制其中一个名字。
5. 写到 `%USERPROFILE%\.skillrouter\credentials.json`。`XHS_COOKIES` 可以是这段 Cookie 头，也可以是 Netscape `cookies.txt` 的完整路径（浏览器扩展 “Get cookies.txt LOCALLY” 可以导出）。两个 Skill 都要在 `grants` 里点名：

```json
{
  "values": {
    "XHS_COOKIES": "a1=你的值; web_session=你的值; webId=你的值"
  },
  "grants": {
    "example.xiaohongshu-web": ["XHS_COOKIES"],
    "example.ytdlp": ["YTDLP_COOKIES", "XHS_COOKIES"]
  }
}
```

路径写法示例：`"XHS_COOKIES": "C:\\Users\\你的用户名\\xhs-cookies.txt"`。yt-dlp 会把它当作 `--cookies`。也可以不写文件，改设环境变量 `SKILLROUTER_CRED_XHS_COOKIES`，授权规则不变。父进程里的同名变量不会自动透传。`credentials.json` 用 UTF-8 保存即可，带 BOM 也能读。

小红书 Skill 会把这段 Cookie 放进 cookie jar，短链跳到 `/explore/` 或 `/discovery/item/` 时仍然带上。笔记页按 `note.noteDetailMap`、`firstNoteId`、`noteCard`、`noteData` 读取；状态里没有笔记对象时用 `og:title`、`og:image`、`description` 和作者 meta。成功结果里的小红书 `page_url` 一律收成 `https://www.xiaohongshu.com/explore/<id>`，不管是网页 Skill 还是 yt-dlp 返回的。签名参数和 Cookie 内容都不会出现在结果里。

`NEED_AUTH` 不计入健康分，也不推动熔断或自动停用。旧版本写进 `state.json` 的停用（没有授权指纹，或来自缺凭证、或来自后来已经改掉的解析失败）会在升级后自动恢复，不必先 `restore`。Skill 自己的 `version` 一变，停用和自动停用的计时也清空。授权值发生变化同样会重新参与排序。`skillrouter restore <skill_id>` 仍可恢复这个 Skill 的全部能力；带上能力和平台则只恢复那一条。

要看它到底有没有把 Cookie 发出去，在运行前设 `SKILLROUTER_DEBUG=1`。日志在 `%USERPROFILE%\.skillrouter\logs\debug.jsonl`。路由器记下授权变量的名字和长度。小红书 Skill 记下每一跳的主机名和路径（没有查询）、HTTP 状态、命中的检测器、Cookie 头长度，以及 `__INITIAL_STATE__` 的键和类型（最多三层，没有值）和出现了哪些 meta 名字。没有 Cookie 的值，也没有笔记正文。

## 体检和健康

`skillrouter doctor` 用 registry 里的公共链接真跑一遍，输出 `skillcall.health.v1`。报告里没有 cookie、密钥、本机路径和用户自己的链接。下载产生的文件在校验后删除。

健康分按 (Skill, 能力, 平台) 分开记。成功 1.0，`UPSTREAM_CHANGED` 记 0，超时和内部错误记 0.2，限流和上游暂时错误记 0.6。链接不支持、内容不存在、缺凭证、地区封锁、质量不够，不计入分数。半衰期 3 天。样本少于 3 个是 `unknown`。

10 分钟内连续 3 次计入分数的失败会熔断 15 分钟。到期后半开，试一次；再失败则时长翻倍，上限 6 小时。

免费 Skill 排在付费前面。付费和按量计费默认不调用，除非 `allow_paid` 或 `--allow-paid`。

## Windows

帮助文字是 ASCII。PowerShell 5 截获外部程序输出时按 OEM 代码页解码，中文帮助会乱码；JSON 结果仍然是 UTF-8。交互窗口里看中文标题，先让控制台使用 UTF-8：

```powershell
chcp 65001
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
python -m skillrouter list
```

路由器会把自己的标准流和子进程都设成 UTF-8（`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`）。入口命令写 `$PYTHON`，在 Windows 上会换成实际的 `python.exe`，不依赖 `python3` 这个名字。超时结束进程时用 Job Object，不需要安装 pywin32。

## 示例 Skill

| 目录 | id | 作用 |
|---|---|---|
| `skills/douyin-dl` | `example.douyin-dl` | 用户原来的 `douyin_dl.py`，脚本本体不改，薄适配层只走 iesdouyin |
| `skills/douyin-ies` | `example.douyin-ies` | 另一份 iesdouyin 实现，作为抖音的后备 |
| `skills/ytdlp` | `example.ytdlp` | yt-dlp。当前提取器覆盖抖音、哔哩哔哩、小红书、微博。没有 ffmpeg 且必须合流时返回 `DEPENDENCY_MISSING` |
| `skills/bilibili-web` | `example.bilibili-web` | 哔哩哔哩网页接口和 WBI。机房 IP 经常 412，这时返回 `GEO_BLOCKED` |
| `skills/kuaishou-web` | `example.kuaishou-web` | 快手公开分享页 |
| `skills/xiaohongshu-web` | `example.xiaohongshu-web` | 小红书视频笔记和图文笔记。短链含 `xhslink.cn`。视频笔记的图片能力返回 `UNSUPPORTED_URL` |
| `skills/weibo-web` | `example.weibo-web` | 微博公开视频和图片。访客 cookie 由 Skill 自己申请 |
| `skills/bilibili-subtitle` | `example.bilibili-subtitle` | 哔哩哔哩 CC / AI 字幕。没有字幕轨就让 ASR 接上 |
| `skills/faster-whisper` | `example.faster-whisper` | 本地 faster-whisper。需要视频时调用 `video.download@1`，不导入其他 Skill |
| `skills/gemini-asr` | `example.gemini-asr` | Gemini `gemini-4-argon`，`cost: paid`。要 `allow_paid`，并且把 `GEMINI_API_KEY` 放进 grants 和 overrides |

`douyin_dl.py` 自己的命令行用法还在，见该目录的 `SKILL.md`。

## 开发

```bash
pip install -e ".[skills,dev]"
pytest
pytest -m live
```

`skills` 里的 `requests` 是部分单测要导入的示例 Skill 依赖。

默认不跑需要外网的测试。`registry/` 以后可以原样拆成独立仓库；wheel 里会带一份快照，离线也能用。环境变量 `SKILLROUTER_REGISTRY` 和 `SKILLROUTER_HOME` 可以改位置。

## 合规

只处理用户当场给出的一条公开链接，供个人查看。不要批量抓取，不要重新上传，不要对外提供托管。平台条款和版权优先于这个工具。
