# OpenSpec 工作方式

OpenSpec CLI 管理集成、变更和归档；职责、安全边界与读取路由以 [AGENTS.md](../AGENTS.md) 为准。能力索引见 [specifications.md](specifications.md)。

## 文档层级

- 当前规范：`openspec/specs/<capability>/spec.md`。
- 活动变更：`openspec/changes/<change>/`，包含 proposal、design、specs、tasks。
- 历史证据：`openspec/changes/archive/`，仅用于理解决策和过去验收，不代表当前配置。
- CLI 上下文与 artifact 规则：`openspec/config.yaml`；已移除 legacy `openspec/project.md` 和 `openspec/AGENTS.md`，有用的架构事实保留在配置和能力索引中。

根层指引负责授权与质量门槛；CLI 上下文只补充架构事实和路由，避免复制操作流程。

## 按任务读取

1. 从能力索引选择受影响的 baseline，不默认读取全部规范或历史。
2. 对已有变更，通过 CLI 查询 status、instructions、指定 delta 和 tasks。
3. 编码前核对相关实现和测试；规范漂移应明确记录并修正，不用陈旧文本覆盖安全门槛。
4. 实现完成后同步 delta、运行验证；只有明确要求时归档。

历史证据适用 `openspec/changes/archive/AGENTS.md`；不重写过去的验收结论来宣称当前生产已验收。

## CLI 与技能

本仓库已用 **OpenSpec CLI 1.14.0** 更新 Claude Code 和 Codex 集成。

- Codex 技能源：`.agents/skills/openspec-*/SKILL.md`。
- Claude Code 本地集成：`.claude/skills/openspec-*/SKILL.md` 与 `.claude/commands/opsx/`，由 CLI 生成；该目录按仓库策略忽略。
- 已移除重复的 `.codex/skills/openspec-*/SKILL.md` 定制副本。
- `CLAUDE.md` 仅导入 `@AGENTS.md`，不复制项目指令。
- 经授权，用户级 custom profile 保留 propose/explore/apply/archive 并补充 update；4 个旧 Codex 全局 opsx prompts 已核对后移除。

`openspec-update-change` 技能修订已有变更的规划文件，不修改代码；它与更新 Agent 集成的 `openspec update` CLI 命令不是同一件事。

遵循当前技能和 CLI 帮助，不维护第三份提案/执行/归档流程。换新环境时用 CLI 更新本地集成，不手工改生成技能。

常用查询与验证：

```bash
openspec list --json
openspec list --specs
openspec status --change "<change>" --json
openspec instructions apply --change "<change>" --json
openspec validate "<change>" --strict
openspec validate --all --strict --no-interactive
```

其他工作流先查看相应技能和 `openspec <command> --help`，不猜测参数。

## 集成更新、规范同步、归档

三者独立：

- `openspec update` 更新 Agent 集成，不同步规范也不归档。
- `openspec-sync-specs` 技能由 Agent 语义合并 delta 到 baseline；不是 `openspec sync` 子命令。
- `openspec archive <change>` 校验并归档，可按 CLI 提示同步规范；已经同步时应报告零变更。

归档前确认 artifacts/tasks 完成、delta 与 baseline 一致、验证通过并检查目标目录。保留 CLI 校验和确认，不用跳过验证掩盖差异；未完成任务或未接受的验收条件必须显式报告。

同步应保留不受影响的要求和场景。整改旧 baseline 的实现漂移需单独说明范围，不能混入某项 delta 的历史验收。

## 当前状态

以下两项已于 **2026-10-04** 通过 CLI 归档；两次同步检查均报告 baseline 已一致、零规范修改：

- [harden-publication-retention](../openspec/changes/archive/2026-10-04-harden-publication-retention/)
- [consolidate-shared-primitives-and-test-fidelity](../openspec/changes/archive/2026-10-04-consolidate-shared-primitives-and-test-fidelity/)

当前无活动变更。此次另外修正旧缓存、GitHub release、调度和编排 baseline，使其描述 complete fingerprint、单一 signing index、验证后 promotion、缓存命中仍发布及只读 sync。记录见 [documentation-audit.md](documentation-audit.md)。

离线回归不等于真实 Apple 变更、签名、发布或设备安装验收；历史证据保持原样。
