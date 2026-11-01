# Skillcall 项目家族规划与 AI 会话交接文档

> 文档用途：记录 `capability-spec`、`skill-eval`、`skill-router` 三个项目的产品定位、技术边界、实施顺序和已做决定。后续开启新的 AI 会话时，应先让 AI 阅读本文，再基于当前仓库状态继续规划或开发。
>
> 当前日期：2026-11-01
> 当前实施状态：monorepo 迁移进行中；`capability-spec 0.4.0` 和 `skill-eval 0.1.0` 已形成可构建包，`skill-router 0.3.8` 暂保留在仓库根目录
> 开发环境：Python 3.12.16，项目虚拟环境为 `.venv`
> 协议名称：`skillcall`

## 1. 项目愿景

目标不是再做一个 Skill 目录或 MCP Server 列表，而是建立一个跨 Agent、Skill、MCP、API 和本地工具的能力体系：

1. 用统一契约描述“用户需要的能力”，而不是绑定具体 Skill。
2. 用持续评测提供带环境、版本和时间戳的可靠性证据。
3. 由 Router 根据任务、质量、成本、延迟、安全策略和实时健康状态选择实现。
4. 当某个实现失效、退化或不符合要求时，自动切换到等价实现。

核心链路：

```text
用户任务
  → Capability
  → 候选 Implementation
  → Evaluation Evidence
  → Policy + Runtime Health
  → Route
  → Execute
  → Validate
  → Fallback
```

项目的长期差异化不应是“拥有更多 Skill”，而应是：

> 基于真实评测和运行反馈，有证据地选择当前最适合完成任务的能力实现。

## 2. 已确定的产品结构

三个项目应当对外独立发布、独立宣传，同时组成一个产品家族。实施阶段先在同一 monorepo 稳定接口，再根据维护和发布需求拆成独立 Git 仓库：

```text
capability-spec
描述一种能力及其实现
        ↓
skill-eval
验证实现是否可靠、有效
        ↓
skill-router
根据评测和运行状态选择实现
```

建议未来建立统一 GitHub Organization：

```text
skillcall/
├── capability-spec
├── skill-eval
└── skill-router
```

当前实施目录：

```text
skill-router/
├── packages/
│   ├── capability-spec/   # 独立 Python 包 skillcall-spec 0.4.0
│   ├── skill-eval/        # 独立 Python 包 skill-eval 0.1.0
│   └── skill-router/      # Router 迁移边界说明
├── src/skillrouter/       # 当前生产 Router，暂不搬迁
├── skills/                # 示例 Implementation
└── tests/                 # Router 与跨包集成测试
```

当前决定：不对 Router 做全量重写。Spec 采用抽取重建，Eval 从零实现，Router 通过兼容路径渐进接入；待跨包接口和回归测试稳定后，再决定是否移动 Router 目录或拆分 Git 仓库。

三个项目的对外定位：

| 项目 | 一句话定位 |
|---|---|
| `capability-spec` | A vendor-neutral capability contract for AI skills and tools |
| `skill-eval` | Test and benchmark Agent Skills, MCP tools and AI capabilities |
| `skill-router` | Route AI tasks to the best available implementation |

建议继续使用现有协议名 `skillcall`，避免重新创造一套术语。

### 2.1 命名与术语策略

项目名称和架构术语采用不同层次：

- `skill-eval` 和 `skill-router` 保留 `Skill`，因为它是当前开发者容易理解和搜索的市场词汇。
- 原计划中的 `skill-spec` 改名为 `capability-spec`，因为它描述的是能力契约，而不是另一种 `SKILL.md` 文件格式。
- `skillcall` 继续作为协议和项目家族名称。
- 整体产品定位使用 **AI Capability Evaluation and Routing Infrastructure**，中文为“面向 AI Agent 的能力评测与路由基础设施”。

文档和代码中的核心术语：

| 术语 | 含义 |
|---|---|
| Capability | 用户希望达到的结果，以及该结果的输入、输出和成功标准 |
| Implementation | Capability 的一种具体实现 |
| Evidence | 某个 Implementation 在指定环境和 Suite 下的评测证据 |
| Route Policy | 根据质量、成本、延迟、安全等约束选择实现的策略 |
| Runtime Health | 实现当前的短期可用状态、失败记录和熔断状态 |

Implementation 可以是：

```text
agent_skill
mcp_tool
http_api
local_command
workflow
remote_agent
```

产品名称中的 Skill 是面向用户的通用称呼；协议和内部数据模型不得假设所有 Implementation 都是 `SKILL.md`。内部核心实体应优先命名为 `Capability`、`Implementation`、`EvaluationReport` 和 `RoutePolicy`，避免设计成一个包揽所有概念的 `Skill` 对象。

## 3. 当前仓库实施状态

### 3.1 capability-spec 0.4.0

位置：`packages/capability-spec/`。

已完成：

- 独立 `pyproject.toml`、CLI、wheel 和 sdist 构建。
- Capability、Implementation、Invocation、Error 四份 JSON Schema。
- 版本兼容规则和 Conformance Validator。
- `video.resolve@1`、`video.download@1`、`image.download@1`、`image.ocr@1`、`video.transcript@1`。
- Registry、平台 ID、标准错误码和 11 个输入输出示例。
- 可独立校验 Spec，也可校验 Router 中 10 个 Manifest、19 个 Provision。

尚未公开发布到 PyPI；“可构建”不等于“已公开发布”。

### 3.2 skill-eval 0.1.0 最小闭环

位置：`packages/skill-eval/`。

已完成：

- 独立 CLI、Command Adapter 和 Remote MCP Streamable HTTP Adapter。
- `skill-eval.suite.v1` 与 `skill-eval.report.v1` JSON Schema。
- Offline/Live 模式隔离、重复运行、超时、环境指纹和内容 Digest。
- Exact、Contains、Regex、Numeric Tolerance、Set Overlap、Exists Scorer。
- Transcript CER/WER Scorer，使用 Unicode NFKC、确定性编辑距离，并把聚合指标写入标准 Report。
- `skill-eval.fixtures.v1` Schema 与转写 Fixture 许可证准入规则；音频资产本身尚未选定或提交。
- 第一轮音频来源审核：LibriSpeech 可作为英文候选；AISHELL-1 需检查归档内许可证；Common Voice 因当前条款要求不要镜像而不提交到仓库。
- Execution Success、Task Success、User Outcome 分离；未测量的 User Outcome 明确为 `null`。
- P50/P95 延迟统计、JSON/Markdown/JUnit Report 和本地 `self-tested` 可信等级；三种格式由同一份标准 JSON Report 派生，不重复执行 Implementation。
- `skill-eval compare` 可校验多个标准 JSON Report 的 Capability 与 Suite Digest，并生成静态 Markdown 对比页。
- Composite GitHub Action 可在调用方 Runner 安装两个独立包、执行指定 Suite，并上传 JSON/Markdown/JUnit 三种证据；仓库 CI 已在真实 Ubuntu/Tesseract Runner 完成 `image.ocr` 实跑，Linux 标准 JSON 证据已固化到 Benchmark 目录。
- wheel 和 sdist 构建验证，以及使用假 Command Implementation 的端到端测试。

尚未完成：

- GitHub Check Reporter。
- 成本采集与预算中止。
- 正式的 `video.transcript` Benchmark 和通过许可证审核的音频 Fixture 资产。
- README Badge 和 GitHub Check Reporter。

### 3.3 skill-router 0.3.8

- `src/skillrouter/engine.py`
- `src/skillrouter/discover.py`
- `src/skillrouter/proc.py`
- `src/skillrouter/creds.py`
- `src/skillrouter/state.py`
- `src/skillrouter/mcp_server.py`
- 运行时排序、回退、健康分和熔断
- 现有 10 个左右示例 Skill

当前 Router 已经可以按 Capability 发现实现，结合成本、平台覆盖、健康状态和熔断进行排序，并在可回退错误发生时尝试下一个实现。

本轮迁移新增：

- 从 `packages/capability-spec/registry/` 读取开发期 Registry。
- Router wheel 继续内置 Registry 与 Schema 快照，保持离线工作。
- 现有 Manifest 无需修改并通过外部 Spec 校验。
- 已可选读取并校验 `skill-eval.report.v1`；只有 Implementation ID、版本、内容 Digest 和 Capability 全部匹配的证据才会附到 `list` 结果。Evidence Loader 与 Runtime Health 存储完全独立，默认路由行为仍未改变。

### 3.4 当前验证基线

- Python：3.12.16，`.venv`。
- `pip check`：无依赖冲突。
- Router/集成非 live 测试：96 passed、1 skipped；8 个 live 测试按默认配置未运行。
- 独立包测试：20 passed；Spec Conformance 覆盖 5 个 Capability、11 个示例，Router 的 10 个 Manifest/19 个 Provision 和 Eval 的 2 个示例 Implementation/2 个 Provision 分别校验。
- `capability-spec`、`skill-eval`、`skill-router` 均已成功构建 wheel；前两个同时验证了 sdist。

## 4. capability-spec 的边界

`capability-spec` 只负责“描述”，不负责执行、评测或路由。它定义的是 Capability Contract，而不是新的 Agent Skill 文件格式。

建议结构：

```text
capability-spec/
├── SPEC.md
├── schemas/
│   ├── capability.schema.json
│   ├── implementation.schema.json
│   ├── invocation.schema.json
│   └── error.schema.json
├── capabilities/
│   ├── video.resolve/
│   ├── video.download/
│   ├── video.transcript/
│   └── image.download/
├── platforms.yaml
├── error_codes.yaml
├── conformance/
└── examples/
```

### 4.1 capability-spec 负责

- Capability ID 和版本规则
- 输入输出 JSON Schema
- Implementation Manifest
- Runtime/Entrypoint 描述
- 标准错误码
- 权限、依赖和副作用声明
- 成本模型声明
- 与 Evaluation Suite 的关联方式
- Conformance Tests

### 4.2 capability-spec 不负责

- 运行 Skill
- 计算健康分
- Provider 排名
- 熔断和回退
- Benchmark 执行
- Marketplace
- 支付和结算

### 4.3 版本策略

- 独立仓库的首个版本建议为 `skillcall spec v0.4`。
- 不重新从 v0.1 开始，以保持现有历史连续性。
- Manifest 中现有的 `skillcall: "0.1"` 可以继续保持，直到声明格式发生破坏性变更。
- `capability-spec` 可以发布小型依赖包，例如 Python 的 `skillcall-spec`；npm 包可在确有需求后增加。

## 5. skill-eval 的产品定义

`skill-eval` 是第一个重点对外宣传的项目，也是长期数据壁垒的主要来源。

正确定位不是“为所有 Skill 给出一个永久总分”，而是：

> 在明确的任务、环境、版本、预算和评测标准下，持续产生可复现的效果证据。

### 5.1 三种成功必须分开

1. Execution Success：是否安装、调用并正常返回。
2. Task Success：输出是否满足任务的可验证要求。
3. User Outcome：结果是否真正对用户有用。

报告中必须分别展示，不应混成一个“Skill 成功率”。

### 5.2 Skill 描述本身也需要评测

- Trigger Precision：不该触发时是否被错误选择。
- Trigger Recall：应该触发时是否能够被发现。
- Claim Verification：描述中声称的能力是否有测试覆盖和实际证据。

### 5.3 建议架构

```text
Skill Adapter
    ↓
Normalized Manifest
    ↓
Evaluation Suite
    ↓
Runner
    ↓
Capability-specific Scorers
    ↓
Evidence Report
```

建议目录：

```text
skill-eval/
├── src/skilleval/
│   ├── cli.py
│   ├── runner.py
│   ├── environment.py
│   ├── report.py
│   ├── adapters/
│   │   ├── command.py
│   │   └── mcp.py
│   ├── scorers/
│   │   ├── schema.py
│   │   ├── assertions.py
│   │   ├── latency.py
│   │   ├── cost.py
│   │   └── transcript.py
│   └── reporters/
│       ├── json.py
│       ├── markdown.py
│       └── junit.py
├── suites/
├── schemas/
├── action/
├── examples/
└── tests/
```

### 5.4 第一版 Adapter

第一版只支持：

- 本地 Command Skill
- Remote MCP Server

后续再考虑：

- HTTP/OpenAPI
- Python Function
- TypeScript Function
- Agent-to-Agent
- 浏览器或 GUI Agent

### 5.5 第一版 Scorer

- JSON Schema
- Exact Match
- Contains/Regex
- Numeric Tolerance
- Set Overlap
- 现有 Expression Assertions
- Execution Success
- Latency
- Cost
- Transcript WER/CER

第一版不把通用 LLM Judge 作为核心。以后增加 Judge 时，必须记录 Judge 模型版本、Rubric、证据和校准信息，不能只返回一个没有解释的分数。

### 5.6 Offline 和 Live 必须分离

Offline Evaluation：

- 使用固定 Fixture、快照或 Mock
- 每个 PR 都可以运行
- 用于发现 Skill 自身回归
- 结果应尽量可复现

Live Evaluation：

- 调用真实 Provider、网站或 API
- 用于检测可用性、延迟、价格和外部退化
- 可以每天或每周运行
- 不应与 Offline 分数混合

## 6. skill-eval 的执行与 UGC 模型

作为个人开发者，不应自己承担所有执行环境。核心模式是：

> Local-first + Bring Your Own Environment + 标准报告 + 社区复现。

### 6.1 执行位置

- 用户本地电脑
- GitHub Actions
- GitLab CI/Jenkins
- 企业自托管 Runner
- 用户自己的 Kubernetes 或内部环境

模型和第三方 API 使用 BYOK，Secret 默认不上传。

### 6.2 第一阶段不要提供公共云端任意代码执行

陌生 Skill 的托管执行会带来：

- 恶意脚本和供应链攻击
- Secret 窃取
- 挖矿和网络扫描
- 文件系统攻击
- API 账单失控

第一阶段服务器只负责：

- 校验结构化报告
- 验证来源和签名
- 聚合统计
- 生成 Badge
- 展示排行榜或历史趋势

官方隔离执行环境应放到更晚阶段。

### 6.3 UGC 参与者

Skill 作者贡献：

- Manifest
- 基础回归测试
- 推荐环境
- 已知限制
- 自测报告

Benchmark 作者贡献：

- 测试数据
- 标准答案
- Scorer
- 任务覆盖说明

普通用户贡献：

- 不同 OS、模型、地区和 Runtime 的复现结果

企业用户贡献：

- 私有 Suite、私有 Skill 和内部环境评测；默认不公开

### 6.4 可信等级

```text
self-tested
→ ci-attested
→ community-reproduced
→ platform-verified
```

每份报告必须绑定：

- Skill ID、版本和内容 Digest
- Suite ID、版本和内容 Digest
- Runner 版本
- 模型/Provider 版本
- 环境指纹
- 时间戳
- 可信来源

排行榜不使用某次最高分，而应优先展示最近时间窗口中的中位数、P10/P90、失败率和环境覆盖。

## 7. skill-eval 报告格式

建议定义独立格式 `skill-eval.report.v1`：

```json
{
  "schema": "skill-eval.report.v1",
  "subject": {
    "id": "example.faster-whisper",
    "version": "1.0.0",
    "digest": "sha256:..."
  },
  "capability": "video.transcript@1",
  "suite": {
    "id": "skillcall.video-transcript.offline",
    "version": "1.0.0",
    "digest": "sha256:..."
  },
  "environment": {
    "os": "macos",
    "arch": "arm64",
    "python": "3.12",
    "runtime": "command",
    "region": "cn"
  },
  "results": {
    "execution_success_rate": 0.98,
    "task_accuracy": 0.92,
    "p50_latency_ms": 8100,
    "p95_latency_ms": 14200,
    "average_cost_usd": 0
  },
  "trust": {
    "level": "ci-attested",
    "issuer": "github-actions"
  },
  "verified_at": "2026-11-01T10:00:00Z"
}
```

报告输出格式至少包括：

- JSON：机器读取和 Router 消费
- Markdown/HTML：人类阅读
- JUnit XML：CI 集成
- GitHub Check：PR 状态
- README Badge：传播和作者激励

## 8. skill-router 的边界

`skill-router` 负责生产请求的发现、选择、调用、回退和实时状态。

### 8.1 Router 输入

- Skill/Implementation Manifest
- Capability Spec
- Evaluation Report
- Runtime Health
- User Policy
- 当前凭证、依赖和环境状态

### 8.2 Router 负责

- 发现候选实现
- Capability 和平台匹配
- 权限、依赖、成本和策略过滤
- 读取 Eval Evidence
- 结合当前运行状态排序
- 调用和超时控制
- 按错误码回退
- 熔断和半开恢复
- 记录运行时结果
- 输出选择理由

### 8.3 Router 不负责

- 维护 Benchmark 标准答案
- 运行完整离线评测
- 定义 Capability Schema
- 给 Skill 发布永久总分

### 8.4 长期评测与实时健康的区别

```text
Evaluation Evidence：
最近 7 天的 100 次标准任务，准确率 93%。

Runtime Health：
最近 10 分钟连续失败 3 次，暂时熔断。
```

`skill-eval` 负责前者；`skill-router` 负责后者。

### 8.5 初期路由评分

第一版可以使用透明加权模型：

```text
route_score =
    task_match       × 0.25
  + eval_accuracy    × 0.30
  + availability     × 0.15
  + latency_score    × 0.10
  + cost_score       × 0.10
  + security_score   × 0.10
```

权重应由 Policy 决定，而不是宣称存在唯一正确的全局排名。

可支持：

- `balanced`
- `highest_quality`
- `lowest_cost`
- `low_latency`
- `local_first`
- `enterprise_security`

## 9. 项目依赖关系

依赖必须保持单向：

```text
capability-spec
   ↑           ↑
skill-eval   skill-router
   ↓
Evaluation Report
   ↓
skill-router
```

规则：

- `capability-spec` 不依赖另外两个项目。
- `skill-eval` 可以依赖 Spec Schema，但不能依赖 Router 才能运行。
- `skill-router` 依赖 Spec Schema，并可选读取 Eval Report。
- 实现作者不采用完整 `capability-spec` 时，`skill-eval` 应尽量通过 Adapter 支持现有 `SKILL.md`、MCP、OpenAPI 或其他格式。

## 10. 第一个公开 Benchmark

不建议把“平台视频下载”作为第一宣传案例，因为它受到链接删除、登录、风控、地区、版权和平台条款影响，难以形成稳定标准答案。

建议使用现有 `video.transcript@1` 作为第一套正式 Benchmark。

现有候选实现：

- `example.faster-whisper`
- `example.gemini-asr`

准备可合法分发的固定音频样本：

- 普通话
- 英文
- 中英混合
- 背景噪声
- 多人说话
- 数字、日期和专有名词
- 简体/繁体
- 不同音频长度

指标：

- 中文 CER
- 英文 WER
- Segment Timing Error
- Execution Success Rate
- P50/P95 Latency
- Cost per Audio Minute
- Memory Usage
- Cold-start Time

示例结果：

```text
Capability: video.transcript@1

Implementation       CER    Success   P95     Cost/min
faster-whisper       8.2%   98%       14.2s   local
gemini-asr           6.5%   96%        8.1s   $0.0x
```

第二套可以使用 `video.resolve` 做 Live Availability 测试，用来展示外部平台变化和 Skill 过时问题，但不与离线正确性分数混合。

### 10.1 第二个离线 Benchmark：image.ocr@1

为避免整个评测体系只覆盖音频转写，第二个确定性离线 Benchmark 使用 `image.ocr@1`。

选择原因：

- 本地和托管实现丰富，可比较质量、成本、延迟、隐私和本地优先策略。
- Fixture 可以由仓库代码生成，不依赖授权不清晰的图片或系统字体。
- 可复用 CER、Exact、Contains 和版面块断言，不需要通用 LLM Judge。
- 能验证文件输入、可选边界框、置信度和多语言提示等协议能力。

第一阶段候选实现：

- `example.tesseract-ocr`
- `example.paddleocr`

第一阶段覆盖：英文大写、项目名、版本号和 ISO 日期，现已包含干净、低对比度和 3 度旋转三个确定性场景。后续再增加中文、噪声、多块阅读顺序和表格，不因新增复杂样本削弱确定性基础用例。

当前状态：`image.ocr@1` Capability、输入输出 Schema、三个代码生成的 PNG Fixture、Fixture Manifest、Offline Suite 以及两个候选 Implementation 已加入并实跑。本机 Tesseract 5.5.4 与隔离环境中的 PaddlePaddle 3.3.1/PaddleOCR 3.7.0 在三个场景的 Execution/Task Success 均为 100%；Tesseract 平均 CER 14.81%、P50 约 87ms，PaddleOCR 平均 CER 0%、P50 约 2.02s。PaddleOCR Adapter 同时兼容 2.x 旧结果结构和 3.x `OCRResult`。标准 JSON 证据与 `skill-eval compare` 生成的静态 Markdown 对比页已提交到 Benchmark 目录；这些仍是单机开发证据，不是正式跨环境排行榜结果。

第三个候选 Capability 为 `document.text_extract@1`，应在 OCR Benchmark 稳定后再定义，避免同时扩张过多协议面。

## 11. 12 周实施路线图

### 第 1～2 周：冻结边界并拆分 capability-spec

状态：**已完成当前 monorepo 阶段目标**。Spec 已成为独立包；Git 仓库拆分延后到接口稳定之后。

目标：

- 为当前 `skill-router 0.3.8` 建立稳定基线。
- 建立独立 `capability-spec` 包，并保留未来拆仓能力。
- 迁移 `SPEC.md`、Registry、Schema、错误码和示例。
- Router 支持读取外部 Spec，同时保留 wheel 内置快照。
- 保证现有 Skill Manifest 不需要修改。

交付建议：

- `capability-spec v0.4.0`
- `skill-router v0.3.9`（尚未发布；当前代码版本仍为 0.3.8）

验收：

- 独立校验一个 `skill.capability.yaml`。
- Router 在联网和离线状态都能工作。
- 当前能力和示例 Skill 行为保持不变。

### 第 3～5 周：skill-eval MVP

状态：**进行中**。Command/MCP Adapter、Suite/Report Schema、确定性 Assertions、Transcript CER/WER、重复运行、环境指纹和 JSON/Markdown/JUnit Report 已完成。

目标：

- 独立 CLI
- Command Adapter
- MCP Adapter
- Suite YAML Schema
- 确定性 Assertions
- Offline/Live 模式
- 重复运行和统计
- 环境指纹
- 成本和超时限制
- JSON/Markdown/JUnit 报告

交付建议：

- `skill-eval v0.1.0`

验收命令目标：

```bash
skill-eval run ./skills/faster-whisper \
  --suite suites/video-transcript/offline.yaml
```

### 第 6 周：GitHub Action 与第一次公开发布

状态：**进行中**。Composite GitHub Action、三格式 Artifact 上传、CI Badge 和可复制示例已加入；Ubuntu/Tesseract OCR Benchmark 已在真实 GitHub-hosted Linux x86_64 Runner 运行成功，下载的 Artifact 通过 Report Schema 校验。仍需正式转写 Benchmark 和第一次公开版本发布。

目标：

- `skill-eval-action`
- README Badge
- 示例项目
- 第一个公开 Benchmark
- 一篇介绍评测方法和结果的技术文章

宣传主题建议：

> We benchmarked local and hosted transcription Skills across accuracy, latency and cost.

### 第 7～8 周：Router 接入 Eval

状态：**进行中**。Router 已完成 Eval Report Schema 内置、配置/环境变量/CLI 加载、严格适用性检查和 `list` 证据展示；默认关闭的 Eval-aware Policy 只比较同一 Offline Suite Digest 下至少两个有效候选，并在成本、覆盖和 Runtime Health 之后按 Task/Execution Success、平均成本与 P95 延迟排序。Policy 已支持最低质量、最高延迟、最高成本软门槛，以及跨环境 `conservative`/`latest` 聚合；被门槛降级的候选仍保留为 fallback。无法比较时自动回退原排序；每次调用返回策略、候选顺序、Evidence 摘要、选择理由和 fallback 状态。下一步用正式多实现 Benchmark 验证策略，并增加按 Capability 定义的指标 Profile。

目标：

- Router 导入 `skill-eval.report.v1`
- 增加 Eval-aware Ranking
- 区分 Evaluation Evidence 与 Runtime Health
- 支持质量、成本和延迟 Policy
- 输出选择理由和证据来源

交付建议：

- `skill-router v0.4.0`

### 第 9～10 周：社区贡献流程

状态：未开始。

目标：

- Suite 贡献模板
- Skill 接入模板
- Report 提交模板
- CI 报告校验
- 可信等级
- CODEOWNERS
- Benchmark 维护规则
- 数据集和 Fixture 许可证要求

第一阶段仍不托管执行社区上传的任意代码。

### 第 11～12 周：第二能力与静态比较页

状态：提前启动。`image.ocr@1` 的契约、确定性 Fixture、Offline Suite、两个候选实现的单机结果，以及由标准 Report 生成的静态 Markdown 对比页已完成；下一步扩充多语言和退化场景，并在 CI 中跨环境复跑。

目标：

- 增加第二套 Capability Benchmark。
- 用 CI 生成静态 Markdown/HTML 比较页。
- 展示最近验证时间、准确率、成功率、延迟、成本、环境覆盖和历史趋势。
- 在确有用户需求前，不开发复杂社区网站。

## 12. 各项目首个版本范围

### 12.1 capability-spec v0.4

当前状态：必须项已全部实现并通过构建与一致性测试，尚未公开发布。

必须有：

- Capability Schema
- Implementation Manifest Schema
- Error Schema
- 五个现有 Capability（含新增的 `image.ocr@1`）
- Conformance Tests
- 版本兼容规则

暂不做：

- Marketplace
- 组织权限
- 收费和分成协议
- 复杂继承系统
- 通用语义本体

### 12.2 skill-eval v0.1

当前状态：Command/MCP Adapter 纵向闭环、JSON/Markdown/JUnit Reporter、Composite GitHub Action、CI Badge、真实 GitHub Runner 证据和首个 OCR Benchmark 已实现；正式转写 Benchmark 和公开发布尚未完成，因此不能视为 v0.1 发布完成。

必须有：

- Command/MCP Adapter
- Suite 格式
- 确定性 Scorer
- Offline/Live
- 重复运行与统计
- 标准报告
- GitHub Action
- 一个正式 Benchmark

暂不做：

- 公共云端任意代码执行
- 通用 LLM Judge
- 全行业排行榜
- 企业权限系统
- 复杂反作弊系统

### 12.3 skill-router v0.4

必须有：

- 保留当前功能
- 消费 Eval Report
- 可解释路由
- Policy
- Fallback
- 运行时健康与长期评测分离

暂不做：

- 托管 Marketplace
- OAuth Connector 平台
- 企业控制台
- 统一支付
- 大规模云端 Gateway

## 13. 成功指标

前三个月不要只看 GitHub Star。

### capability-spec

- 第三方实现能否在 30 分钟内完成接入。
- Schema 是否足以表达现有示例 Skill。
- 新实现接入是否不需要修改 Router 专用代码。

### skill-eval

- 至少评测 5 个实现。
- 至少有 2 套独立 Suite。
- 本地和 GitHub Actions 能生成结构一致的报告。
- 出现至少一个外部贡献的测试用例或环境复现。
- 成功发现至少一次真实的 Skill 或 Provider 退化。

### skill-router

- Eval-aware Routing 是否比固定顺序提高任务成功率。
- Fallback 恢复了多少失败请求。
- 每次选择是否能够解释原因和证据。
- 新实现接入是否不需要修改 Router 核心代码。

## 14. 当前仓库迁移原则

当前 `src/skillrouter/engine.py` 体量较大，不应在拆仓库的同时进行大规模重写。

安全顺序：

1. ~~冻结当前稳定基线。~~ 已完成，以 `skill-router 0.3.8` 为行为基线。
2. ~~提取 Spec 和 Registry，保持 Router 行为不变。~~ 已完成。
3. ~~为外部 Spec 增加加载方式并保留 wheel 快照。~~ 已完成。
4. 抽取独立 Eval Runner 和 Report Schema。进行中，Command/JSON 闭环已完成。
5. 完成正式 Benchmark 和 Reporter，再让 Router 可选接入 Eval Report。
6. Router Eval-aware 行为稳定后，再移动 Router 包目录或逐步拆分 `engine.py`。

不要同时进行：

- 仓库拆分
- 协议重命名
- Router 大重构
- 大量新 Capability 开发
- 云端平台建设

## 15. 精力分配

未来 12 周建议：

```text
skill-eval      50%
capability-spec 20%
skill-router    30%
```

`skill-eval` 负责获取开发者关注并积累评测数据；`capability-spec` 提供公共契约；`skill-router` 是现有真实消费者和未来商业入口。

## 16. 下一步任务

优先执行：

1. 按现有 `skill-eval.fixtures.v1` Schema 选择并审核可合法分发的普通话、英文、中英混合、噪声和多人语音资产，记录许可证、来源和校验和。
2. 使用现有 CER/WER Scorer 建立正式 `video.transcript` Offline Suite。
3. 为 `image.ocr` 增加许可证清晰的中文、噪声和多块阅读顺序 Fixture，并在 CI 中复跑已完成的 Tesseract/PaddleOCR 对比报告。
4. 用 `faster-whisper` 和 `gemini-asr` 运行首个转写 Benchmark；托管模型继续使用 BYOK。
5. 基于已验证的 Composite Action 增加 README Badge 与独立示例项目（已完成），下一步准备版本发布说明和外部仓库接入示例。
6. 为不同 Capability 定义可选的指标 Profile，避免用同一质量指标解释 OCR、转写和下载。
7. 用更多正式 Benchmark 证据验证 Eval-aware Ranking；Evaluation Evidence 与 Runtime Health 继续保持独立。

## 17. 给后续 AI 会话的工作指令

开始工作前：

1. 阅读本文、`README.md`、`SPEC.md` 和相关代码。
2. 检查当前 Git 状态，保留用户已有修改。
3. 激活 `.venv` 并确认 Python 为 3.12；不要使用 macOS 系统 Python 3.9。
4. 不假设本文之后仓库没有变化，以代码和最新测试为准。
5. 先说明本次工作属于 `capability-spec`、`skill-eval` 还是 `skill-router`。
6. 保持三个项目的单向依赖和职责边界：Spec 不依赖其他包，Eval 不依赖 Router。
7. 新 Eval 功能优先写在 `packages/skill-eval/`；不要把 Benchmark 或长期评测逻辑放回 Router。
8. 优先完成可运行的纵向闭环，避免先建设通用云平台。
9. 新增 Eval 功能时，优先使用确定性规则；LLM Judge 只能作为补充。
10. 不在公共服务器执行不可信 Skill，除非已经完成明确的隔离和安全设计。
11. Evaluation Report 必须包含环境、版本、Suite、时间戳和证据来源。
12. Runtime Health 和 Evaluation Evidence 不得混为同一指标。

本项目最重要的产品原则：

> Spec 描述能力，Eval 提供证据，Router 使用证据做决策。
