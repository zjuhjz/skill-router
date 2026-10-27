# Skill 统一调用标准（v0.3）

> 状态：已定稿（v0.3，实现 0.3.8）。v0.2 第 9 节的决定继续有效。v0.3 把当时推迟的转写和图片能力落地，并补上本地文件、嵌套调用和 Windows 命令行。0.3.1 补上模型预热、简体转写和分享链接。0.3.2 更换过一次哔哩哔哩字幕样本。0.3.3 补上小红书短链 `xhslink.cn`。0.3.4 抹掉错误信息里的签名参数，并把小红书登录墙记成 `NEED_AUTH`。0.3.5 让登录 Cookie 穿过短链跳转，并抹掉成功结果里的分享参数。0.3.6 修正登录后的笔记页解析，并让缺凭证不再把 Skill 停用。0.3.7 让旧 `state.json` 里的停用在升级后自动失效。0.3.8 让只声明当前平台的 Skill 排在多平台通用 Skill 前面。
> 日期：2026-10-21
> 协议名：**skillcall**。参考实现：**skill-router**（命令行入口 `skillrouter`）。
> 协议声明版本仍是 `skillcall: "0.1"`；本文档版本是 v0.3。

---

## 0. 要解决什么问题

现在的 Skill（带 `SKILL.md` 和脚本的文件夹，Claude / Cursor / OpenClaw 这类 Agent 都在用）和 MCP server 有三个共同问题：

1. **会过期**。平台接口一改，Skill 就坏了，但没有人知道，AI 还在继续调用。
2. **不可替换**。两个作者写的"抖音下载" Skill，输入输出各不一样，坏了一个，AI 没法自动换另一个。
3. **AI 看到的是一堆 Skill，而不是"能力"**。装了 5 个下载 Skill，AI 得自己判断用哪个。

本标准的思路：**AI 只说"我要什么能力"，路由器（router）找出声明了这个能力的 Skill，按健康度挑一个执行，失败就换下一个。**

设计原则：

- **路由器不含任何平台代码。** 怎么解析抖音、怎么绕过风控，全部是 Skill 的事。路由器只负责发现、匹配、排序、调用、回退、记录。
- **对现有 Skill 侵入最小。** 加一个声明文件和一个 JSON 入口就能接入；不装路由器时，Skill 照常单独使用。
- **能力是公共契约，Skill 是可替换的实现。** 同一能力的输入输出由标准定义，不由某个 Skill 定义。
- **健康靠真跑，不靠自报。** 每个 Skill 自带测试样例，路由器定期真跑一遍。

和上一版 `cnvideo` 的区别：`cnvideo` 是把 iesdouyin、yt-dlp、TikHub 等写死在一个包里的"后端"。在本标准下，它们每一个都应该是**独立的 Skill**，`cnvideo` 里的路由、体检、结果结构这几块思路保留下来，挪到路由器和能力定义里。

---

## 1. 能力定义（Capability）

### 1.1 命名

格式：`<domain>.<action>`，全小写，用点分隔，可多级。

| 能力 ID | 含义 |
|---|---|
| `video.resolve` | 解析视频页面，返回元数据和媒体直链，不落盘 |
| `video.download` | 下载视频到本地文件 |
| `video.transcript` | 获取平台字幕或语音转写文本 |
| `video.comments` | 读取一小页公开评论（仍未实现） |
| `image.download` | 下载图文内容里的图片 |

规则：

- **平台不进能力名。** 不写 `douyin.download`，而是 `video.download` + 平台声明。这样同一能力可以跨平台复用一套 schema，路由器也不用知道"抖音"是什么。
- 能力名一旦发布不改，只能新增或废弃。

### 1.2 版本

- 能力用**主版本号**：`video.download@1`。只在破坏性变更时升主版本（删字段、改字段含义、改必填）。
- 新增可选字段不升版本。Skill 和路由器都必须**忽略不认识的字段**。
- Skill 可以同时声明 `@1` 和 `@2`。路由器按调用方要求的版本匹配；调用方不指定时用最新稳定版。

### 1.3 输入输出 Schema

每个能力版本有一份 JSON Schema（draft 2020-12）：`input.schema.json` 和 `output.schema.json`。以 `video.download@1` 为例（节选）：

```jsonc
// input
{
  "url": "https://v.douyin.com/xxxx/",   // 必填：分享链接，或含链接的分享文案
  "quality": "best",                      // 可选：best | 1080p | 720p | smallest
  "prefer_no_watermark": true,            // 可选，默认 true：尽量无水印
  "require_no_watermark": false,          // 可选：true 时有水印必须返回 QUALITY_NOT_MET
  "min_height": 720                       // 可选：低于该分辨率返回 QUALITY_NOT_MET
}
// 输出目录不在 input 里，由路由器通过 context.output_dir 给出（见 3.2）
```

```jsonc
// output
{
  "meta": {
    "platform": "douyin",          // 平台 ID，见 1.6
    "id": "7412345678901234567",
    "title": "...",
    "author": "...",
    "author_id": "...",
    "duration_ms": 105000,
    "cover": "https://...",
    "published_at": "2026-10-01T12:00:00Z",
    "page_url": "https://www.douyin.com/video/...",
    "stats": { "plays": null, "likes": 1200, "comments": 88, "shares": 10, "favorites": 300 }
  },
  "files": [
    {
      "path": "video.mp4",          // 相对 context.output_dir 的路径
      "role": "video",              // video | audio | muxed | cover | subtitle
      "size_bytes": 73400320,
      "sha256": "…",                // 可选
      "has_watermark": false,       // 可选，null = 不确定
      "width": 1080, "height": 1920
    }
  ],
  "extra": {}                       // Skill 私有字段，路由器不解释，调用方别依赖
}
```

**硬约束**：`video.download@1` 成功时，`files` 里必须至少有一个 `role: "muxed"`（音画合一）的文件。B 站这类音视频分离的平台，由 Skill 自己用 ffmpeg 合流；只拿到分离的流就不算成功，返回 `DEPENDENCY_MISSING` 或 `QUALITY_NOT_MET`。否则两个"都通过 schema"的 Skill，一个给能用的 mp4，一个给没声音的半成品，就谈不上可替换。

`video.resolve@1` 的输出是 `meta` + `media[]`（直链列表，字段沿用 cnvideo 的 `MediaUrl`：`url / quality / container / has_watermark / role / width / height / bitrate / extra_urls`），没有 `files`。

`video.transcript@1` 的输入是视频 URL 或本地文件 `file`（二选一），可选 `language`。输出是 `language / source(platform_subtitle|asr) / text / segments[{start_ms, end_ms, text}]`。

`image.download@1` 的输出是 `meta` + `files[]`，至少一张 `role: "image"` 的 `.jpg/.jpeg/.png/.webp/.gif`。多张图用 `index` 区分。

这些字段大部分直接取自 cnvideo 的 `cnvideo.result.v1`，已经在抖音和 B 站上验证过够用。

### 1.4 标准错误码

Skill 失败时必须返回下列错误码之一。**错误码决定路由器要不要换下一个 Skill**，这是可替换性的关键。

| 错误码 | 含义 | 路由器动作 | 计入健康分 |
|---|---|---|---|
| `INVALID_INPUT` | 输入不符合 schema | 路由器在调用前就用 schema 校验，不合法直接报给 AI；Skill 运行中报的 `INVALID_INPUT` 按 `UNSUPPORTED_URL` 处理（换下一个），防止一个 Skill 误报就截断回退链 | 否 |
| `UNSUPPORTED_URL` | 这个 Skill 不认这个链接 | 换下一个 | 否 |
| `NOT_FOUND` | 内容不存在、已删除、私密 | 再试 1 个**不同实现**确认，仍是 NOT_FOUND 则停止 | 否 |
| `NEED_AUTH` | 需要登录 cookie / API key 而没有或已失效 | 换下一个；提示用户补凭证 | 否（但标记"凭证缺失"） |
| `RATE_LIMITED` | 被限流、验证码、风控 | 换下一个；本 Skill 短暂冷却 | 轻度计入 |
| `GEO_BLOCKED` | IP/地区被拒（如机房 IP 被 B 站 412） | 换下一个 | 否（记录网络类型） |
| `UPSTREAM_CHANGED` | 平台接口或页面结构变了，解析失败 | 换下一个 | **重度计入** |
| `UPSTREAM_ERROR` | 平台 5xx、网络错误等临时问题 | 换下一个 | 轻度计入 |
| `TIMEOUT` | 超时（通常由路由器判定） | 换下一个 | 计入 |
| `DEPENDENCY_MISSING` | 缺少二进制或 Python 包（如 ffmpeg） | 换下一个；提示安装 | 否（标记"未就绪"） |
| `QUOTA_EXCEEDED` | 付费 API 额度用完 | 换下一个 | 否 |
| `QUALITY_NOT_MET` | 做得到，但达不到 `require_no_watermark` / `min_height` | 换下一个；全都达不到时，返回最好的那份降级结果并在 `hints` 里说明（需 Skill 在 `details` 里附上已产出的文件） | 否 |
| `INTERNAL` | Skill 自身 bug、未知异常、输出不合法 | 换下一个 | 计入 |

说明：

- 用户原先提的 `NEED_COOKIES` 合并进 `NEED_AUTH`，`error.details.auth_kind` 写 `cookies | api_key | login`。
- 错误对象格式：`{ "code": "RATE_LIMITED", "message": "人看的说明", "retry_after_s": 60, "details": {} }`。`message` 不得包含 cookie、key 等凭证。
- 不认识的错误码按 `INTERNAL` 处理。

### 1.5 能力定义放在哪：Registry 仓库

一个小的公共 Git 仓库（暂名 `skillcall-registry`），只放定义，不放实现：

```
skillcall-registry/
  spec/                     # 本文档
  capabilities/
    video.download/
      1/
        capability.yaml     # 名称、说明、状态(stable|draft|deprecated)、错误码子集、routing 规则
        input.schema.json
        output.schema.json
        examples/           # 合法输入输出样例，供 Skill 作者自测
    video.resolve/1/...
    video.transcript/1/...
  platforms.yaml            # 平台 ID 列表，见 1.6
  conformance/              # 一致性测试：给一个 Skill，验证它的输出是否符合 schema
```

`capability.yaml` 里的 `routing` 告诉路由器怎么从输入里认出平台，这样路由器不用假设每个能力都有 `url` 字段：

```yaml
# capabilities/video.download/1/capability.yaml（节选）
id: video.download
version: 1
status: draft
routing:
  platform_field: /platform      # 输入里显式给了平台就直接用
  url_field: /url                # 否则从这个字段提取 URL，再查 platforms.yaml
  extract_url_from_text: true    # 允许传整段分享文案（见 4.2）
# 平台无关的能力（如将来的 video.transcode）写 routing: none
```

路由器自带一份 registry 快照，可离线工作；联网时可更新。新增能力走 PR。

### 1.6 平台 ID

`platforms.yaml` 统一平台 ID 和常见 URL 形态，避免一个写 `douyin`、另一个写 `tiktok_cn`：

```yaml
douyin:      { name: 抖音,   hosts: [douyin.com, v.douyin.com, iesdouyin.com] }
bilibili:    { name: 哔哩哔哩, hosts: [bilibili.com, b23.tv, m.bilibili.com] }
kuaishou:    { name: 快手,   hosts: [kuaishou.com, v.kuaishou.com, chenzhongtech.com] }
xiaohongshu: { name: 小红书, hosts: [xiaohongshu.com, xhslink.com, xhslink.cn] }
weibo:       { name: 微博,   hosts: [weibo.com, weibo.cn, video.weibo.com] }
```

每个平台还可以配 `canary_urls`：社区共同维护的体检用链接（选官方账号的常驻视频，至少 2 条），Skill 可以直接引用，不必各自写死一条迟早会被删的链接（见 5.1）。

这是 registry 里的**数据**，不是路由器代码，不违反"路由器不含平台代码"的原则。

---

## 2. Skill 声明格式

### 2.1 放在哪

**推荐：在 Skill 文件夹里加一个旁挂文件 `skill.capability.yaml`**，`SKILL.md` 一个字不用改。

可选：在 `SKILL.md` frontmatter 的 `metadata` 里加一行指针，方便人看：

```yaml
---
name: douyin-download
description: ...
metadata:
  skillcall: skill.capability.yaml
---
```

为什么不把整块声明直接写进 frontmatter：部分 Agent 的 Skill 校验只允许 `name / description / license / allowed-tools / metadata` 等固定字段，塞大段自定义字段可能被拒或被截断；而且声明里有健康样例、正则，写在 frontmatter 里会让 `SKILL.md` 很难读。旁挂文件对现有 Agent 完全透明。

### 2.2 字段

```yaml
skillcall: "0.1"                 # 声明格式版本
id: io.github.hujiazhen.douyin-ies    # 全局唯一，建议反向域名或 github 路径
version: 1.2.0                   # Skill 自身版本（semver）
name: 抖音下载（iesdouyin 直解析）
author: { name: Hu Jiazhen, url: https://github.com/... }
license: MIT
homepage: https://github.com/...

provides:
  - capability: video.download@1
    platforms: [douyin]                    # 平台 ID，来自 registry
    url_patterns:                          # 可选，比平台更细的匹配；正则
      - '^https?://(v\.)?douyin\.com/'
      - '^https?://(www\.)?iesdouyin\.com/share/video/\d+'
    entrypoint:
      type: command                        # command | mcp | http
      command: [$PYTHON, skillcall_entry.py, download]   # 相对 Skill 目录；$PYTHON 见下
    timeout_s: 120
    requires:
      env: []                              # 必需的环境变量名（只写名字）
      optional_env: [DOUYIN_COOKIES]
      binaries: []
      python: ["requests>=2.31"]           # 仅供展示和安装提示，路由器不做静态检查
      network: true
    cost: free                             # free | paid | metered
    side_effects: [writes_files, network]  # 供安全策略使用
    health_check:
      use_platform_canaries: true          # 用 registry 里抖音的公共体检链接
      samples:                             # 也可以自带
        - input: { url: "https://v.douyin.com/e3x2fjE/" }
      expect:
        - "output.meta.platform == 'douyin'"
        - "output.files[0].size_bytes > 100000"
      interval_h: 12

  - capability: video.resolve@1
    platforms: [douyin]
    entrypoint: { type: command, command: [$PYTHON, skillcall_entry.py, resolve] }
    timeout_s: 30
    cost: free
    health_check:
      use_platform_canaries: true
      expect: ["len(output.media) >= 1"]
```

字段说明：

- `id`：路由器靠它区分 Skill，健康记录也按它存。改名等于新 Skill。
- `provides[]`：一个 Skill 可以提供多个能力、多个平台。**健康分按 (Skill, 能力, 平台) 三元组分别记**，抖音坏了不影响同一 Skill 的 B 站分数。
- `entrypoint`：见第 3 节。命令数组的第一个元素可以用占位符 `$PYTHON`、`$NODE`、`$UV`，由路由器换成本机真实路径。原因是 Windows 上通常没有 `python3` 这个命令，写死会直接失败。
- `requires.python`：只用于提示和安装，路由器不检查。推荐 Skill 用 `uv run --script`（PEP 723 内联依赖）或自带 `.venv`；入口脚本捕获 `ImportError`，返回 `DEPENDENCY_MISSING`，不要让它崩成 `INTERNAL` 去扣健康分。
- `requires.env` / `optional_env`：只写变量名，用来**申请**凭证，不等于自动拿到（见 8.2）。缺了必需变量，路由器直接跳过该 Skill（记为 `NEED_AUTH`，不调用、不扣分）。
- `cost`：`paid / metered` 的 Skill 默认排在所有免费 Skill 之后，且需用户在配置里显式允许（沿用 cnvideo 对 TikHub 的做法，防止悄悄花钱）。
- 将来可加 `deprecated: true` / `superseded_by: <id>`，由作者主动宣布停更并指向替代者。
- `health_check.expect`：断言用一个**极小的表达式子集**：路径取值（`output.a.b[0]`）、`== != > >= < <=`、`len()`、`exists()`、`matches(path, regex)`。不允许任意代码，避免体检变成执行入口。

### 2.3 兼容性

- 不装路由器：Agent 照常读 `SKILL.md`，按里面的说明直接跑脚本。
- 装了路由器：路由器读 `skill.capability.yaml`，通过 `entrypoint` 调用。
- 同一个 Skill 的老 CLI（如 `douyin_dl.py URL --json`）保留不动，标准入口另加一个薄适配层即可（见 7.1）。

---

## 3. 调用契约

### 3.1 command 入口（必须支持，最通用）

路由器在 Skill 目录下启动子进程，**stdin 写入一个 JSON 请求，stdout 读一个 JSON 响应**。

请求：

```json
{
  "skillcall": "0.1",
  "request_id": "r_01JB…",
  "capability": "video.download@1",
  "input": { "url": "https://v.douyin.com/e3x2fjE/", "quality": "best" },
  "context": {
    "output_dir": "/home/u/.skillrouter/runs/r_01JB…/out",
    "temp_dir": "/home/u/.skillrouter/runs/r_01JB…/tmp",
    "timeout_s": 120,
    "locale": "zh-CN",
    "mode": "invoke",
    "raw_input_text": "7.43 复制打开抖音，看看【…的作品】https://v.douyin.com/e3x2fjE/ …",
    "proxy": null,
    "max_output_bytes": 4294967296,
    "client_cwd": "/home/u/work"
  }
}
```

`mode` 为 `invoke`（正常调用）或 `health_check`（体检，Skill 可借此跳过缓存）。`raw_input_text` 是用户粘贴的原始分享文案（路由器已从中提取出纯 URL 放进 `input.url`）。`proxy` 是用户配置的代理，同时也以 `HTTPS_PROXY` 等环境变量传入。`client_cwd` 是路由器进程的工作目录。Skill 解析相对的本地文件路径时用它，因为子进程的 cwd 是 Skill 目录。

Skill 可以再调用路由器，取得别的能力的结果，但不得导入另一个 Skill 的代码。做法是执行同一个解释器：`python -m skillrouter invoke <capability> --url … --save-to <目录> --yes`。路由器把 `SKILLROUTER_HOME`、`SKILLROUTER_REGISTRY`、`SKILLROUTER_SKILL_PATHS`（未设置时用本次发现到的目录）、`PYTHONPATH` 等控制变量传给子进程，不传凭证值。嵌套深度记在 `SKILLROUTER_CALL_DEPTH`，达到 3 就停止。`--yes` 只表示这次嵌套调用沿用用户已经确认过的信任，不把密钥写进命令行。

成功响应：

```json
{ "ok": true, "output": { "meta": {…}, "files": [ … ] }, "logs": "可选，简短" }
```

失败响应：

```json
{ "ok": false, "error": { "code": "UPSTREAM_CHANGED", "message": "_ROUTER_DATA 中缺少 videoInfoRes", "details": {} } }
```

规则：

1. stdin/stdout 一律 UTF-8。路由器给子进程设 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，否则中文 Windows 默认 GBK，中文标题会乱码或报错。stdout **只能**输出这一个 JSON 对象。进度和日志写 stderr，路由器截取尾部 4 KB 存日志（先脱敏）。
2. 退出码：`0` = 成功；`2` = 已处理的失败（stdout 里有 error 对象）；其他非零 = 崩溃，路由器记为 `INTERNAL`。stdout 有合法 error 对象时以 error 为准。
3. 超过 `timeout_s`，路由器终止**整个进程树**（POSIX 用进程组发 SIGTERM，5 秒后 SIGKILL；Windows 用 Job Object 整体结束），避免 ffmpeg 之类的孙进程残留，记为 `TIMEOUT`。
4. 文件只能写到 `output_dir` / `temp_dir`。`files[].path` 必须是相对 `output_dir` 的路径；路由器检查 `realpath(output_dir/path)` 确实在 `output_dir` 内、是普通文件（不是符号链接）、大小一致，否则判 `INTERNAL`。写出超过 `max_output_bytes` 也判失败。
5. 成功后路由器把文件移到用户指定的 `save_to`，统一命名为 `<platform>_<meta.id>[_<role>].<ext>`，同名已存在就追加 `_1`、`_2`，绝不覆盖；返回给 AI 的 `path` 换成最终绝对路径。每次尝试结束（成功、失败、超时都算）立即删除 `temp_dir`；失败的尝试同时删除 `output_dir`。
6. 路由器用能力的 `output.schema.json` 校验输出，不合法记 `INTERNAL`。**这一步保证了不同作者的 Skill 真的可以互换。**
7. 凭证只通过环境变量传入（见第 8 节），不出现在 stdin 请求里。

### 3.2 mcp 入口（可选）

Skill 本身就是一个 MCP server 时：

```yaml
entrypoint:
  type: mcp
  command: [node, server.js]       # stdio 方式启动；或 url: 走 streamable HTTP
  tool: douyin_download            # 对应的 MCP 工具名
  input_map: identity              # 工具参数与能力 input 相同；不同则写映射
```

路由器作为 MCP client 调这个工具：优先取结果里的 `structuredContent`（MCP 2025-06 版规范起支持），没有则把 `content[0].text` 当 JSON 解析，再按能力的 output schema 校验。工具返回 `isError` 时，从内容中解析 `code`，解析不出记 `INTERNAL`。stdio 方式的 MCP server 由路由器复用进程，空闲 5 分钟自动关闭。MCP 生态里已有的工具因此可以不改代码、只写一个声明文件就接进来。

### 3.3 http 入口（可选）

```yaml
entrypoint: { type: http, url: "http://127.0.0.1:8787/skillcall", method: POST }
```

请求体和响应体与 3.1 完全相同。HTTP 状态码 200 = 有响应体（看 `ok`），其他按 `UPSTREAM_ERROR` 处理。适合常驻服务或远程付费 API 的包装。默认只允许 `127.0.0.1` / `localhost`；指向外部域名必须声明 `side_effects: [third_party_service]` 并经用户确认；一律拒绝链路本地地址（如 `169.254.169.254` 云元数据）和未授权的内网地址，防止 Skill 借路由器探测内网。

---

## 4. 路由器行为

### 4.1 发现

启动时及每次调用前（带缓存，文件变化才重扫）扫描 Skill 目录：

- 默认：`~/.skillrouter/skills/`、`~/.claude/skills/`、`~/.cursor/skills/`、项目内 `.skills/`、配置里追加的路径。
- 只认含 `skill.capability.yaml` 的文件夹；没有声明的 Skill 不管，Agent 仍可单独使用。
- 解析失败或引用了 registry 里不存在的能力：跳过并在 `doctor` 里报出。

### 4.2 匹配

对一次 `invoke(capability, input)`：

1. 能力 ID 和主版本一致。
2. 先用能力的 input schema 校验输入，不合法直接返回 `INVALID_INPUT`，不启动任何 Skill。
3. 按能力的 `routing` 规则认平台：输入里有 `platform` 就用；否则从 `url` 字段里**提取** URL（用户常粘贴整段分享文案，如"7.43 复制打开抖音…https://v.douyin.com/xxx/"），用纯 URL 替换 `input.url`、原文放进 `context.raw_input_text`，再取 host 查 `platforms.yaml`。平台须在 Skill 的 `platforms` 里，且（若写了）命中 `url_patterns`。
4. `requires` 满足：必需环境变量存在、二进制在 PATH 里。不满足的记为"未就绪"并跳过。
5. 用户策略允许：付费 Skill 需显式开启；用户可在配置里禁用某个 Skill。

### 4.3 排序

候选集合按以下顺序排序（前一项相同才比下一项）：

1. **本次指定**（`prefer_skill`）。
2. **用户置顶**（配置里的 `pin`）。
3. **成本**：`free` > `metered` > `paid`。
4. **覆盖面**：`platforms` 只写当前这一个平台的 Skill，排在写了多个平台或 `*` / `any` 的通用 Skill 前面。健康未知（样本少于 3）不算差，不能输给已经健康的通用 Skill。样本不少于 3 且健康分低于 50 时，这条优先取消，通用 Skill 排到前面。已经停用、熔断打开或冷却中的不进入候选。
5. **熔断状态**：同一覆盖面里，闭合先于半开。半开的专用 Skill 仍先于闭合的通用 Skill。
6. **健康等级**：`healthy` > `unknown` > `degraded` > `failing`（`deprecated` 默认不参与）。
7. **健康分**（0–100，见 5.2），高者优先。
8. **延迟**：近期 p50 低者优先。
9. 用户 `priority` 数字。
10. **解析质量**：仅能力名以 `.resolve` 结尾。sidecar 的 `metadata: rich` 先于 `basic` 或未写。下载和其他能力不看这个字段。
11. `id` 字典序，保证结果稳定。

把成本放在覆盖面和健康之前，是为了不因为免费 Skill 偶尔失败，或免费的通用 Skill 声明了很多平台，就默认走付费通道。付费 Skill 只在免费的都失败时兜底。这一点可以讨论（见第 9 节）。

### 4.4 回退

- 按排序依次调用，遇到 1.4 表中"换下一个"的错误码就继续。
- 输入不合法在调用前就被拦下（4.2 第 2 步），不会进入回退链；`NOT_FOUND` 再试一个不同 Skill 确认后停止。
- 默认最多尝试 4 个 Skill，总时长上限 300 秒（可配置）。
- 全部失败时返回 `ALL_FAILED`，附完整尝试记录，并给出**可操作的提示**：例如"2 个 Skill 需要抖音 cookie，设置 DOUYIN_COOKIES 后可用"。

### 4.5 熔断

按 (Skill, 能力, 平台) 维护：

- 10 分钟内连续 3 次计入健康分的失败 → **打开**，15 分钟内不调用。
- 到期后**半开**：下一次请求允许试 1 次，成功则关闭，失败则再打开且时长翻倍（上限 6 小时）。
- `RATE_LIMITED` 带 `retry_after_s` 时，直接按该值冷却。

### 4.6 日志

每次调用写一条 JSONL 到 `~/.skillrouter/logs/calls-YYYYMMDD.jsonl`：

```json
{"ts":"2026-10-20T10:48:00Z","request_id":"r_01JB…","capability":"video.download@1",
 "platform":"douyin","url_hash":"sha256:9f1c…","attempts":[
  {"skill":"io.github.hujiazhen.douyin-ies","version":"1.2.0","ok":false,"code":"UPSTREAM_CHANGED","latency_ms":2140},
  {"skill":"io.github.someone.douyin-kukutool","version":"0.4.1","ok":true,"latency_ms":5320}],
 "result":"ok"}
```

- 链接默认只存哈希，不存原文（用户可配置开启）。
- 真实调用结果同样喂给健康分（被动健康），不只是体检。

---

## 5. 体检（doctor）与健康分

### 5.1 主动体检

- `skillrouter doctor`：对所有 Skill 的每个 `health_check` 样例真跑一次，用 `mode: health_check`。
- 默认每 12 小时（取 Skill 声明的 `interval_h`）后台跑一次；也可手动或用 cron。
- 下载类体检只校验后立即删除文件。
- 样例返回 `NOT_FOUND` 时换下一条样例；全部 `NOT_FOUND` 则记为 `canary_expired`（提醒更换体检链接），**不算** Skill 失败。断言不通过但 Skill 返回成功，则算 `INTERNAL`。
- **惰性体检**：很多人不会常驻后台进程。路由器在处理正常请求时，如果发现某个 Skill 处于 `degraded` / `failing` 且距上次体检已超过 `interval_h`，顺手在后台补跑一次，让坏掉又被修好的 Skill 有机会恢复。
- 每次体检记录**网络环境**：`residential | datacenter | unknown` 和粗略地区。cnvideo 实测发现 B 站对机房 IP 返回 412，同一 Skill 在家用宽带上是好的，所以健康结论必须带网络标签，否则会误判。

### 5.2 健康分

每个 (Skill, 能力, 平台) 一个 0–100 分，来源是主动体检 + 真实调用：

- 每次结果给一个分值：成功 1.0；`UPSTREAM_CHANGED` 0；`TIMEOUT / INTERNAL` 0.2；`RATE_LIMITED / UPSTREAM_ERROR` 0.6；`NEED_AUTH / GEO_BLOCKED / UNSUPPORTED_URL / NOT_FOUND / DEPENDENCY_MISSING / QUOTA_EXCEEDED / QUALITY_NOT_MET` 不计入。
- 指数衰减加权平均，**半衰期 3 天**：越新的结果权重越大，这样坏了能很快掉分，修好后也能很快恢复。
- Skill 的 `version` 或文件哈希变化（作者发了修复版）时，清空该 Skill 的历史分数、解除熔断和 `deprecated`，回到 `unknown`。
- 健康等级：样本少于 3 个 = `unknown`；≥ 80 = `healthy`；40–80 = `degraded`；< 40 = `failing`。

### 5.3 标记废弃

满足任一条件，路由器把该 (Skill, 能力, 平台) 标记为 `deprecated`，不再参与默认排序，并在 `doctor` 和 `list_capabilities` 里提示"可以卸载/有替代":

- 连续 14 天 `failing`，且期间至少有 5 次分布在不同日期的真实体检（防止只因为没人体检而被误废弃）；
- Skill 作者在声明里写了 `deprecated: true` 或 `superseded_by: <id>`；
- registry 里该能力版本被标为 deprecated。

用户可以手动恢复。

### 5.4 可分享的健康报告

`skillrouter doctor --report health.json` 输出 `skillcall.health.v1`：

```json
{
  "schema_version": "skillcall.health.v1",
  "generated_at": "2026-10-20T10:48:00Z",
  "router": { "name": "skill-router", "version": "0.3.8" },
  "network": { "kind": "residential", "region": "CN" },
  "checks": [
    { "skill": "io.github.hujiazhen.douyin-ies", "skill_version": "1.2.0",
      "capability": "video.download@1", "platform": "douyin",
      "ok": true, "latency_ms": 8900, "code": null, "checked_at": "…" }
  ]
}
```

报告**不含** cookie、key、本机路径、用户真实调用的链接，只含体检样例的结果。以后多个用户自愿上传，就能汇总成公开的"哪个 Skill 现在还能用"看板。这正是现有 registry（MCP Registry、Glama、Smithery、ClawHub）没做的：它们只收录，不做真实可用性检测。

---

## 6. AI 看到的是什么

AI 不直接看到各个 Skill，只看到路由器。路由器以两种形式暴露：

### 6.1 MCP server（主推）

只有 3 个固定工具，与装了多少个 Skill 无关：

| 工具 | 作用 |
|---|---|
| `list_capabilities` | 列出当前可用的能力、支持的平台、每个平台的健康状态和"需要什么凭证" |
| `describe_capability(capability)` | 返回该能力的输入 schema、说明和示例 |
| `invoke(capability, input, options?)` | 调用。`options` 可选：`prefer_skill`、`allow_paid`、`max_attempts`、`save_to` |

`invoke` 的返回：

```json
{
  "ok": true,
  "capability": "video.download@1",
  "output": { "meta": {…}, "files": [{ "path": "C:/Users/e_ifox/Downloads/douyin_7412….mp4", … }] },
  "served_by": { "skill": "io.github.someone.douyin-kukutool", "version": "0.4.1" },
  "attempts": [ … ],
  "hints": []
}
```

可选：配置 `expose: per_capability` 时，路由器额外把每个能力暴露成一个独立工具（如 `video_download`），参数直接用能力的输入 schema。对工具选择能力弱的模型更友好，但工具数会随能力增多。默认关闭。

### 6.2 Skill 形式（给不支持 MCP 的 Agent）

路由器自身也带一个 `SKILL.md`，教 Agent 用命令行：

```bash
skillrouter list
skillrouter invoke video.download --input '{"url":"https://v.douyin.com/xxxx/"}' --save-to ./downloads
```

输出与 MCP `invoke` 返回相同的 JSON。

---

## 7. 完整示例

### 7.1 改造现有 Skill：`douyin_dl.py`

现状：`douyin_dl.py` 内部写死了三级回退（iesdouyin → videofetch/KuKuTool → yt-dlp），CLI 参数是 `URL -o DIR --json`，失败时打印 `ERROR:` 并退出 1。

改造建议分两步：

**第一步（最小改动，当天可做）**：加一个 `skillcall_entry.py` 适配层，只调用 iesdouyin 这一路，不碰原脚本的 CLI：

```python
# skillcall_entry.py —— 读 stdin JSON，调用 douyin_dl，写 stdout JSON
import json, sys
from pathlib import Path
import douyin_dl as d

def fail(code, msg):
    print(json.dumps({"ok": False, "error": {"code": code, "message": msg}}, ensure_ascii=False))
    sys.exit(2)

req = json.load(sys.stdin)
action = sys.argv[1]                     # download | resolve
url = req["input"]["url"]                # 路由器已校验并从分享文案中提取出纯 URL
out = Path(req["context"]["output_dir"])
s = d._session()
try:
    aweme_id, final = d.resolve_aweme_id(url, s)
except d.DouyinDownloadError as e:
    fail("UNSUPPORTED_URL", str(e)[:300])
except Exception as e:                    # requests 网络错误等
    fail("UPSTREAM_ERROR", str(e)[:300])
try:
    meta = d.parse_iesdouyin(aweme_id, s)
except Exception as e:
    fail("UPSTREAM_CHANGED", str(e)[:300])     # 实际应细分：网络错误→UPSTREAM_ERROR，已删除→NOT_FOUND
m = {"platform": "douyin", "id": meta["aweme_id"], "title": meta.get("desc"),
     "author": meta.get("author"), "duration_ms": meta.get("duration_ms")}
if action == "resolve":
    print(json.dumps({"ok": True, "output": {"meta": m,
          "media": [{"url": u, "role": "muxed", "has_watermark": False} for u in meta["play_urls"]]}},
          ensure_ascii=False)); sys.exit(0)
dest = out / f"{meta['aweme_id']}.mp4"
size = 0
for u in meta["play_urls"]:
    try:
        size = d.download_file(u, dest, s); break
    except Exception:
        continue
else:
    fail("UPSTREAM_ERROR", "所有播放地址都下载失败")
print(json.dumps({"ok": True, "output": {"meta": m, "files": [
      {"path": dest.name, "role": "muxed", "size_bytes": size, "has_watermark": False}]}}, ensure_ascii=False))
```

（示意代码。`resolve_aweme_id`、`parse_iesdouyin`、`download_file` 及返回字段 `aweme_id / desc / author / duration_ms / play_urls` 已与当前 `douyin_dl.py` 核对；错误分类在正式版中要更细，例如 `_ROUTER_DATA` 解析失败才算 `UPSTREAM_CHANGED`，视频已删要映射成 `NOT_FOUND`。）

再加上 2.2 节那份 `skill.capability.yaml`，这个 Skill 就接入了。原来的 `douyin-download.md` / `SKILL.md` 和 `python3 douyin_dl.py URL --json` 用法完全不变。

**第二步（推荐）**：把原脚本里的 videofetch 和 yt-dlp 两级回退拆成两个独立 Skill。回退交给路由器，每一路有自己的健康分；哪一路坏了一眼可见，别人写的同类 Skill 也能插进同一条回退链。

### 7.2 另一个作者的抖音 Skill（可替换）

```yaml
# douyin-kukutool/skill.capability.yaml
skillcall: "0.1"
id: io.github.someone.douyin-kukutool
version: 0.4.1
name: 抖音下载（KuKuTool 第三方解析）
author: { name: someone }
license: MIT
provides:
  - capability: video.download@1
    platforms: [douyin, kuaishou]
    entrypoint: { type: command, command: [$PYTHON, main.py] }
    timeout_s: 90
    requires: { python: [videofetch], network: true }
    cost: free
    side_effects: [writes_files, network, third_party_service]
    health_check:
      use_platform_canaries: true
      expect: ["output.files[0].size_bytes > 100000"]
```

作者不同、实现不同，但都声明 `video.download@1` + `douyin`，输出都过同一份 schema 校验，所以路由器可以互相替换。`side_effects` 里的 `third_party_service` 表示链接会发给第三方，用户可以在策略里禁止。

### 7.3 B 站 Skill（带凭证）

```yaml
# bilibili-web/skill.capability.yaml
skillcall: "0.1"
id: io.github.hujiazhen.bilibili-web
version: 0.1.0
name: B 站下载（Web API + WBI 签名）
license: MIT
provides:
  - capability: video.download@1
    platforms: [bilibili]
    entrypoint: { type: command, command: [$PYTHON, entry.py, download] }
    timeout_s: 180
    requires:
      binaries: [ffmpeg]                 # DASH 音视频分离，必须合流成 muxed 才算成功
      optional_env: [BILIBILI_COOKIES]   # 有 cookie 可拿 1080P 以上
      network: true
    cost: free
    health_check:
      samples:
        - input: { url: "https://www.bilibili.com/video/BV1GJ411x7h7" }
      expect: ["output.meta.platform == 'bilibili'", "output.files[0].size_bytes > 100000"]
```

### 7.4 一次带回退的调用

AI 调用：

```json
{ "tool": "invoke", "arguments": {
  "capability": "video.download@1",
  "input": { "url": "https://v.douyin.com/e3x2fjE/" },
  "options": { "save_to": "C:/Users/e_ifox/Downloads" } } }
```

路由器内部：

1. host `v.douyin.com` → 平台 `douyin`。
2. 候选：`douyin-ies`（healthy, 92 分）、`douyin-kukutool`（healthy, 81 分）、`douyin-ytdlp`（degraded, 55 分，需 cookie，未设置 → 跳过）、`douyin-tikhub`（paid，未开启 → 跳过）。
3. 调 `douyin-ies` → 2.1 秒后返回 `UPSTREAM_CHANGED`（抖音改了页面结构）。记一次失败，健康分降到 71。
4. 调 `douyin-kukutool` → 5.3 秒成功，输出通过 schema 校验，文件移到 `save_to`。

返回给 AI：

```json
{
  "ok": true,
  "capability": "video.download@1",
  "output": {
    "meta": { "platform": "douyin", "id": "7412345678901234567", "title": "…", "author": "…", "duration_ms": 105000 },
    "files": [{ "path": "C:/Users/e_ifox/Downloads/douyin_7412345678901234567.mp4", "role": "muxed", "size_bytes": 73400320 }]
  },
  "served_by": { "skill": "io.github.someone.douyin-kukutool", "version": "0.4.1" },
  "attempts": [
    { "skill": "io.github.hujiazhen.douyin-ies", "ok": false, "code": "UPSTREAM_CHANGED",
      "message": "_ROUTER_DATA 中缺少 videoInfoRes", "latency_ms": 2140 },
    { "skill": "io.github.someone.douyin-kukutool", "ok": true, "latency_ms": 5320 }
  ],
  "skipped": [
    { "skill": "io.github.x.douyin-ytdlp", "reason": "NEED_AUTH", "hint": "设置 DOUYIN_COOKIES 后可用" },
    { "skill": "io.github.x.douyin-tikhub", "reason": "PAID_NOT_ALLOWED" }
  ],
  "hints": ["douyin-ies 刚刚失败（UPSTREAM_CHANGED），可能需要更新"]
}
```

AI 不需要知道换过 Skill，只需拿到文件；需要时能从 `attempts` 看到原因。

---

## 8. 安全与信任

1. **Skill 是代码，装了就能执行。** 路由器不能让它更安全，只能做到：
   - 第一次调用某个新装或升级过的 Skill 前，要求用户确认一次（显示 `id`、作者、`side_effects`、`requires`）。
   - 子进程使用最小环境：只传基础变量（POSIX：`PATH HOME LANG LC_ALL TMPDIR SSL_CERT_FILE`；Windows 另加 `SystemRoot SYSTEMDRIVE USERPROFILE APPDATA LOCALAPPDATA TEMP TMP PATHEXT COMSPEC`，缺了 Python 的网络和 SSL 会直接出错）、代理变量（`HTTP(S)_PROXY ALL_PROXY NO_PROXY`）和**用户授权过的凭证**。
   - **声明不等于授权。** 恶意 Skill 完全可以在 `optional_env` 里写 `OPENAI_API_KEY`、`GITHUB_TOKEN`、`AWS_SECRET_ACCESS_KEY`。所以凭证不从用户的全局环境变量里透传，而是存在路由器自己的凭证配置里，按 Skill 逐个授权：`DOUYIN_COOKIES → 仅授权给 douyin-ies、douyin-ytdlp`。首次确认时显示该 Skill 申请了哪些变量。另设硬性黑名单（`*_API_KEY` 中的模型厂商 key、`AWS_*`、`GITHUB_*`、`SSH_*` 等），任何 Skill 都拿不到，除非用户逐项手动放行。
   - 工作目录限定在 Skill 目录，输出限定在 `output_dir`。
   - 可选沙箱（后期）：Linux 上用 bubblewrap/容器，macOS 用 sandbox-exec，Windows 暂不做。v0.1 不强制。
2. **凭证只走环境变量**（来源是路由器的凭证配置或系统钥匙串）。不进 stdin 请求、不进日志、不进健康报告；路由器写日志前对所有已注入的凭证值做脱敏替换（沿用 cnvideo 的 `redact` 思路）。
3. **签名与来源（后期）**：Skill 发布时附带 `skill.capability.yaml` 和文件哈希清单的签名（如 Sigstore 或 minisign）。路由器显示"已验证作者 / 未签名"，用户可选择只装已签名的。Skill 内容变化（哈希变了）视为新版本，需要重新确认。
4. **文件安全**：输出路径必须在 `output_dir` 内、不能是符号链接、不覆盖已有文件（见 3.1 第 4、5 条）。
5. **网络**：http 入口防内网探测（见 3.3）。
6. **health_check 不执行任意代码**，断言语言只有取值和比较（见 2.2）。
7. **第三方服务**：把链接发给 KuKuTool、TikHub 这类外部服务的 Skill 必须声明 `third_party_service`，用户可一键禁用。
8. **合规**：能力定义里注明"仅限个人使用、一次一条用户提供的链接"；路由器不提供批量抓取接口。

---

## 9. 已决事项（v0.2）

v0.1 草案把下面 10 件事留作开放问题。v0.2 全部按草案自己的推荐决定，不再保留备选方案。

1. **声明放旁挂文件。** 用 `skill.capability.yaml`。`SKILL.md` 不改。可以在 frontmatter 的 `metadata.skillcall` 放一行指针，不把整份声明写进 frontmatter。
2. **免费优先于付费。** 成本排在健康之前：`free`，然后 `metered`，然后 `paid`。付费和按量计费的 Skill 必须由用户显式允许（`allow_paid`）才会被调用，并且只在免费 Skill 都失败之后兜底。不因为免费实现偶尔失败就改走付费。
3. **NOT_FOUND 要再确认。** 某个 Skill 返回 `NOT_FOUND` 之后，再换 1 个不同实现确认。第二个仍是 `NOT_FOUND` 就停止。第二个是别的结果，就按错误码表继续回退。
4. **AI 只看到 3 个通用工具。** `list_capabilities`、`describe_capability`、`invoke`，外加对等的 CLI。按能力拆工具（`expose: per_capability`）这一版保留开关但默认关闭。
5. **公开健康看板以后再做。** 这一版只产出 `skillcall.health.v1` 报告：不含 cookie、密钥、本机路径和用户真实链接，并带 `network.kind`（`residential`、`datacenter` 或 `unknown`）。匿名上传，以及把家用宽带和机房 IP 分开汇总，都不在本版。
6. **名字不变。** 协议叫 `skillcall`，参考实现叫 `skill-router`，命令行叫 `skillrouter`。
7. **Registry 先由本项目维护。** 新能力通过改 `registry/` 的 PR 进入。以后再交给社区。`registry/` 现在放在本仓库，设计上可以原样拆成独立仓库；路由器只把它当数据读，不在代码里写平台知识。
8. **质量不达标就继续换。** 不满足 `require_no_watermark` 或 `min_height`，或者没有合格的 muxed 文件时，记为 `QUALITY_NOT_MET` 并继续回退。如果每一次实际尝试都是 `QUALITY_NOT_MET`，返回最好的那份降级结果，并在 `hints` 里说明。
9. **凭证按 Skill 授权，必须执行。** 声明了环境变量名不等于会拿到它。值放在路由器自己的凭证配置里，或放在 `SKILLROUTER_CRED_<NAME>`；只有该 Skill id 被明确授权时才注入子进程。硬性黑名单（`AWS_*`、`GITHUB_*`、`SSH_*`、`OPENAI_*`、`ANTHROPIC_*`、`AZURE_*`，以及名字以 `_API_KEY` 结尾的变量）默认不注入，除非用户按变量名逐项放行。
10. **v0.2 先做两个能力。** 当时实现 `video.resolve@1` 和 `video.download@1`。`video.transcript@1` 和 `image.download@1` 留到 v0.3，见下面的增补。

v0.1 正文里已经写进去的审阅结论继续有效，不回退。其中包括：旁挂声明、`$PYTHON` / `$NODE` / `$UV`、Windows 最小环境变量和 Job Object、从分享文案里抽 URL、能力级 routing、`QUALITY_NOT_MET`、muxed 硬约束、平台公共 canary、凭证黑名单、输出路径 realpath 校验、`save_to` 不覆盖、代理、`max_output_bytes`、惰性体检，以及 Skill 版本或文件哈希变化时清空健康史、熔断和 deprecated。

### v0.3 增补

1. **能力。** registry 增加 `image.download@1` 和 `video.transcript@1`。平台仍然不进能力名。
2. **本地文件。** `video.transcript@1` 的 routing 使用 `file_field` 和 `file_platform`。没有 URL 时，平台 id 取 registry 里的 `local`。这是数据，不是路由器里的平台分支。
3. **转写实现互换。** 平台字幕、本地 ASR、付费 ASR 都声明同一个能力。没有字幕轨时返回 `UNSUPPORTED_URL`，让免费 ASR 接上。付费 ASR 仍受 `allow_paid` 和凭证授权约束。
4. **付费密钥。** `GEMINI_API_KEY` 以 `_API_KEY` 结尾，必须出现在该 Skill 的 grant 里，并且写进 `overrides`，才会注入。密钥不得进入 URL、stdout、stderr 或日志。
5. **嵌套调用。** 转写 Skill 需要视频文件时调用 `video.download@1`，不导入其他 Skill。
6. **ffmpeg。** 缺少 ffmpeg 是 `DEPENDENCY_MISSING`（退出码 3），并在 `hints` 里写出原因。不要记成 `INTERNAL`。
7. **命令行。** `invoke` 接受 `--input`、`--input-file` 或 `--url` 里的恰好一个。帮助文字用 ASCII，避免 PowerShell 5 按 OEM 代码页截获 UTF-8 中文时乱码。JSON 结果仍是 UTF-8。
8. **体检。** 构建机上没能打开的公共样本必须在 `platforms.yaml` 里写 `canary_verified: false` 和 `canary_note`。`health_check.enabled: false` 的 Skill 不进入 doctor，避免每次体检都下载模型或调用付费接口。
9. **router 版本。** 参考实现是 skill-router 0.3.8。协议声明版本仍是 `skillcall: "0.1"`。

### v0.3.1 增补

1. **模型预热。** Skill 可在 `skill.capability.yaml` 根上声明 `warmup.timeout_s`。`skillrouter warmup` 和 `doctor --warmup` 用 `context.mode=setup` 调用它，不套 `invoke` 的 `max_total_s`。子进程在下载模型时向 stderr 写 `skillrouter: model-fetch`。这条调用若被超时杀掉，记为 `DEPENDENCY_MISSING`，并采用技能写出的 `model-fetch-hint`，而不是 `TIMEOUT`。
2. **转写脚本。** `video.transcript@1` 的输出可以带 `script`：`Hans`、`Hant`、`Latn`、`und`。中文默认收成简体（`Hans`），除非调用方要 `zh-Hant` / `zh-TW` / `zh-HK`。
3. **小红书分享链接。** 没有 `xsec_token` 的公开笔记页不再当作已验证样本。用户需要提供新的分享链接。registry 里该平台 `canary_verified` 保持 false。

### v0.3.2 增补

1. **哔哩哔哩字幕样本。** `BV1bnfdBpEPs` 在家用网络上返回 `NOT_FOUND`（稿件不可见）。字幕体检曾改到 `BV16h411m7NG`。下载用的 `BV1GJ411x7h7` 和 `BV1xx411c7mD` 不变。

### v0.3.3 增补

1. **小红书短链。** 分享文案里的短链域名包括 `xhslink.cn` 和 `xhslink.com`（路径 `/a`、`/m`、`/o`）。没有 `http://` 时也从这段文案里抽出 URL。长链保留 `discovery/item/<id>` 和 `xsec_token`。
2. **视频笔记的图片能力。** `type=video` 或笔记类型是视频时，`image.download@1` 返回 `UNSUPPORTED_URL`，说明这是视频笔记。页头上的「登录」不算 `NEED_AUTH`；只有最终地址是 `/login` 才算。
3. **字幕 canary。** 家用网络确认 `BV16h411m7NG` 没有 CC / AI 字幕轨。没有替换成另一条未核对的样本，字幕 Skill 的体检保持关闭。

### v0.3.4 增补

1. **参数脱敏。** 错误信息、尝试日志和健康记录会抹掉 `xsec_token`、`xsec_source`、`share_id`、`shareRedId`、`token`、`sign`、`a_bogus`、`msToken`、`cookies`，以及名字里带 `token` 或 `sign` 的查询参数。百分号编码和 JSON 字段同样处理。yt-dlp Skill 在写出错误之前也会抹掉。
2. **小红书登录墙。** 转到 `/login`、验证码或 `/404/sec_` 时返回 `NEED_AUTH`，并提示设置 `XHS_COOKIES`。只有页面写明地区限制才用 `GEO_BLOCKED`。yt-dlp 看到最终地址是登录页时同样返回 `NEED_AUTH`，而不是 `UPSTREAM_ERROR`。
3. **Cookie 授权。** `XHS_COOKIES` 可以是 Cookie 头或 Netscape `cookies.txt` 路径。`example.xiaohongshu-web` 和 `example.ytdlp` 都要在 `grants` 里单独授权后才会注入。

### v0.3.5 增补

1. **Cookie 跟着跳转走。** `requests` 会在重定向时丢掉手写的 `Cookie` 头，只保留 cookie jar。小红书 Skill 把 `XHS_COOKIES` 放进 jar，短链、`/explore/<id>` 和 `/discovery/item/<id>` 都会带上。凭证文件按 UTF-8 读，允许 BOM。
2. **登录墙只看地址。** 最终路径是 `/login`、`/captcha` 或 `/404/sec_` 才是 `NEED_AUTH`。正文或脚本里的「登录」「captcha」不算。笔记页解析失败时，再用同一个 `xsec_token` 试 `/explore/<id>` 和 `/discovery/item/<id>`。
3. **成功结果也脱敏。** `page_url`、`source_url` 以及结果里其余字符串中的签名参数和 Cookie 值都换成 `***`，调用日志和健康记录同样处理。
4. **调试。** `SKILLROUTER_DEBUG=1` 时，路由器记下授权变量的名字和长度；小红书 Skill 把每一跳的路径（不含查询）、HTTP 状态、命中的检测器和 Cookie 长度写到 `~/.skillrouter/logs/debug.jsonl`。不写 Cookie 的内容。

### v0.3.6 增补

1. **登录后的笔记页。** `window.__INITIAL_STATE__` 先把 `undefined` 换成 `null` 再解析。笔记读 `note.noteDetailMap`（先 URL 里的 id，再 `firstNoteId`，再其余键）、`noteCard`，以及 `noteData.data.noteData`。状态里没有笔记时，用 `og:title`、`og:image`、`description` 和作者 meta。视频地址还可以来自 `backupUrls`、`originVideoKey`。`/explore/<id>` 和 `/discovery/item/<id>` 都认。
2. **Skill 自己的调试行。** 调试开关和日志路径放进调用请求的 `context`。小红书 Skill 写入每一跳的主机和路径（没有查询）、状态码、检测器、Cookie 头长度，以及 `__INITIAL_STATE__` 的键和类型（深度不超过 3，没有值）和 meta 标签名字。不写笔记正文，不写 Cookie。
3. **缺凭证不再停用。** `NEED_AUTH` 不计入健康分、熔断和自动停用。授权值发生变化，或上一次就是 `NEED_AUTH`，已经停用的 Skill 会重新参与排序。升级后若还没有记下授权指纹，下一次选择也会清掉卡住的停用标记。`skillrouter restore <skill_id>` 恢复该 Skill 的全部记录；能力和平台仍可一起给出，只恢复一条。
4. **规范页地址。** 平台 registry 可以声明 `id_from_url` 和 `canonical_page`。小红书的 `page_url` 和 `source_url` 收成 `https://www.xiaohongshu.com/explore/{id}`，与哪个 Skill 返回无关。

### v0.3.7 增补

1. **升级解开旧停用。** 选择 Skill 之前，先看 `state.json`。健康记录里的 `skill_version` 或 `content_hash` 和当前 Skill 不一致时，清掉 `deprecated`、熔断、自动停用计时和健康样本。没有 `cred_hash` 的旧记录如果正处于停用或熔断，同样清掉停用，不必先执行 `restore`。`NEED_AUTH` 和授权之前的失败不再把 Skill 留在停用里。
2. **版本变化使停用过期。** 解析器修好并提高 Skill `version` 之后，先前的 `UPSTREAM_CHANGED` 不能继续停用它，自动停用的 14 天计时也重新开始。授权指纹没变、版本也没变时，停用仍然有效。

### v0.3.8 增补

1. **专用 Skill 先于通用 Skill。** 免费候选里，`platforms` 只写当前平台的 Skill 排在写了多个平台或通配符的 Skill 前面。健康分未知时也是这样。只有专用 Skill 被停用，或样本不少于 3 且健康分低于 50，才让通用 Skill 先试。抖音上 `douyin-dl`、`douyin-ies` 仍在 yt-dlp 前面；哔哩哔哩上 `bilibili-web` 仍在 yt-dlp 前面。
2. **解析质量提示。** 每个能力可以写 `metadata: rich` 或 `basic`。它只在能力名以 `.resolve` 结尾时打破平局，不改变下载顺序。

---

## 附：v0.3 实现范围

1. `registry/` 写出 `video.resolve@1`、`video.download@1`、`image.download@1`、`video.transcript@1` 的 schema、错误码表、平台 ID、URL 形态和公共体检链接。快手、小红书、微博的 canary 写在 `platforms.yaml`；构建机未能验证的标 `canary_verified: false`。
2. 示例 Skill 放在 `skills/`，各自独立运行：用户的 `douyin_dl.py`（薄适配层，不改原脚本）、iesdouyin、yt-dlp（yt-dlp 实际支持的平台，另加缺失 ffmpeg 时返回 `DEPENDENCY_MISSING`）、bilibili-web、快手分享页、小红书笔记页、微博公开接口、哔哩哔哩字幕、faster-whisper、Gemini ASR。`faster-whisper` 1.2.1 依赖 `av<19`（PyAV 19 去掉了 `metadata_errors`）。本地 ASR 默认关闭 VAD；`language` 以 `zh` 开头时加简体中文提示。
3. `skill-router` 负责发现、匹配、排序、调用、回退、熔断、日志、doctor 和 MCP。路由器代码不包含平台名字；平台知识只出现在 `registry/` 和 `skills/`。
4. 回退用两个声明了同一能力的 Skill 演示：前一个失败，路由器自动调用下一个。转写也一样：没有字幕就换 ASR。
