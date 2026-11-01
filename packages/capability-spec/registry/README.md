# skillcall capability-spec v0.4 registry

能力契约、错误码、平台 ID 和公共体检链接。路由器把这里当数据读，不在代码里写平台知识。

这个目录与 [`schemas/`](../schemas/) 和 `skillcall_spec.conformance`
共同构成可独立发布的 capability-spec
快照。当前 Registry 快照会打进 `skill-router` 的 wheel，离线也能用。
环境变量 `SKILLROUTER_REGISTRY` 可以指向另一份副本。

当前能力：`video.resolve@1`、`video.download@1`、`image.download@1`、`image.ocr@1`、`video.transcript@1`。新能力通过修改本目录进入。

安装本包后运行 `skillcall-spec validate`，可以校验 Schema、
Capability、示例以及所有 `skill.capability.yaml` 的结构和交叉引用。

`canary_verified` 和 `canary_note` 是给人看的。路由器只读 `canary_urls` 字符串列表。`false` 表示构建机没能确认这条链接还能打开。
