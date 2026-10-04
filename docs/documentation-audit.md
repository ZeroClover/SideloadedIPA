# 文档与 Agent 指令审查记录

本文件是审查证据，按需读取；不作为新的常驻行为指令。

## 初次审查范围与目标假设（历史快照）

目标是 Claude Opus 5.5 与 OpenAI GPT-6 系列共享的仓库指令，不改应用的
模型请求配置。项目不是 LLM API 应用，未发现需要调整的模型调用、tool schema
或 subagent roster；Prompt Audit Group 3/4 不适用。用户明确要求实施文档优化。

审查入口清单为修改前 git 跟踪的 **114 个 Markdown 文件**：4 个 skills、14 个
baseline specs、11 个未归档 change 文档、75 个 archive 文档、10 个其他文档。
另核对 openspec/config.yaml、.env.example、Python/web manifests、CLI、工作流与
关键实现。历史文档的旧命令不执行，也不作为当前指令。用户级 Agent 配置、
凭据值和仓库外配置不在改动范围内。

## 官方依据

1. Anthropic [Prompt Audit 源文](https://github.com/anthropics/skills/blob/8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4/skills/claude-api/shared/prompt-audit.md)
   （通过 gh API 读取源文件，不浏览 GitHub 渲染页）。核心是针对有证据的过时模式
   审查，而非按字数删减；保留上下文、工具契约、安全边界与脆弱操作的精确顺序。
2. Anthropic [Optimizing for cost and intelligence](https://platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence)：
   审查过度验证、矛盾指令、手工 scratchpad 等遗留模式；删除是待验证假设。
3. OpenAI [Rethinking skills and prompts for GPT-6 Astra](https://developers.openai.com/blog/rethinking-skills-and-prompts-for-gpt-6-astra)：
   精确触发、最小路由入口、渐进式披露；避免每次编辑都读完整文档栈。
4. OpenAI [Using GPT-6](https://developers.openai.com/api/docs/guides/latest-model)：
   明确任务完成条件与自治边界，避免旧指令引发无关加载、过度测试或提前停止。

这些是官方指南与 audit rubric，不将其冒称为独立的强制认证规范。
没有执行 Claude Code 的 /claude-api prompt-audit 命令，也没有运行付费跨模型 eval。

## 主要发现与处置

位置按修改前版本（git HEAD）计。Group 1：1 项；Group 2：8 项。
另列用户指定的结构变更与未自动修复的规范差异，避免把组织偏好伪装成模型缺陷。

| 位置与原文证据 | 模式 / 依据 | 置信度、处置 |
| --- | --- | --- |
| openspec/project.md:20,37,44：mypy “non-blocking”、仅 scripts 检查、“PR checks also recognize main” | G2 过时项目事实；现有 pr-checks 强制检查 src 与 scripts，仅 PR master | 高；rewrite 为 capability 路由，检查事实集中 development.md，版本/分支读取工作流 |
| README.md:60：“add --publish to verify / publish” | G2 命令契约错误；cli.py 仅 verify/run 定义此参数；verify 不上传，只延后缓存晋升 | 高；remove 人类概览中的操作细节，正确语义集中 operator-runbook.md |
| MIGRATION.md:63：curl 缺少 POST | G2 过时接口；web/lib/revalidation.ts 对非 POST 返回 405 | 高；remove 旧迁移入口，字段不再支持的事实并入 configuration.md，当前接口集中 publication.md |
| README.md:29 与 docs/configuration.md 环境段：复制 .env 并配置凭据，未说明加载方式 | G2 运行环境说明不完整；CLI 无 dotenv 加载 | 高；rewrite environment.md，明确由进程环境/secret manager 提供；不建议打印值 |
| docs/security.md:10：“Files are cleaned up automatically after run completion” | G2 过宽保障；临时提取会清理，work 中 profiles/cache/manifests 有保留用途 | 高；rewrite 精确说明保留边界，并注明 PIPE 捕获内存不受返回证据字节上限约束 |
| docs/troubleshooting.md:15：“Recalculate … and update ipa_sha256” | G2 安全契约缺口；摘要是 reviewed source identity，不能用失败下载自证可信 | 高；rewrite 为阻断、保留摘要/长度证据、独立核实来源后才能接受新字节 |
| docs/operator-runbook.md:160-162：回退配置后 force_rebuild 即发布旧版 | G2 命令效果过度承诺；GitHub 仍选当前匹配 release | 高；rewrite publication.md，说明须准备 reviewed pinned source/verified retained artifact，并走正常验收 |
| .codex/skills/openspec-archive-change/SKILL.md:69：调用 openspec-sync-specs，并手动 mv archive | G2 缺失依赖/生命周期绕行；仓库无该 skill，CLI archive 自带同步/校验 | 高；rewrite 为审查 delta 后使用 CLI 确认流程，保留 archive 目标与验收边界 |
| 四个 SKILL.md 的固定对话、逐任务输出模板、explore:14 强制退出模式再提案 | G1c 判断任务过度编排；GPT-6 官方建议 outcome/constraints 和 task-local guidance | 中；rewrite 为明确触发、目标、必要依赖顺序与实际阻塞条件；explore 本身仍不实施，新的明确实现请求可改变任务意图 |

最后一项未进行模型行为前后对照；不声称已证实 token 成本、延迟或模型质量改善。
其中 dependency-before-output、verify-before-publish、retirement-before-delete 等
真实依赖顺序保留，不当作“固定步骤”删掉。

## 用户指定的结构与冲突解决

- 新增 AGENTS.md：只放项目语义、行为/授权边界、任务路由与完成条件。
- CLAUDE.md 仅 @AGENTS.md；模块采用普通链接，避免无条件导入所有参考。
- README.md 保留产品能力、使用前提与基本起点，面向人类，不再承担 Agent 规则。
- development/environment/publication/openspec 各自成为单一主题模块；index.md
  只做定位和文档维护路由。原架构/配置/安全/操作/排障内容按责任分离。
- openspec/project.md:23,32 的 backward-compatible migration/compatibility layers
  与用户级项目原则冲突。git blame 显示源于 cca50da 的旧规则；按当前明确项目原则
  移除旧路径，不新增兼容层。这是指令冲突解决，不声称它来自模型偏好。
- 8 个 baseline Purpose 的 TBD 替换为能力摘要；Requirement/Scenario 文本不变。
- 两份 docs 历史记录增加快照标识；75 份 OpenSpec archive 内容保持原样，增加
  scoped AGENTS.md 说明其证据属性。没有为了整洁重写历史验收。

初次审查的改动可通过 git diff 审查；当时未提交、未归档、未部署。最终整改及归档状态见下文。

## 审查时保留的规范差异（历史快照）

第一次同步阶段经用户授权合并两项 delta：3 个 baseline 新增 6 项要求、修改
3 项要求；当时未归档，旧缓存规范尚待审查。后续整改已修正规范并完成归档，
见末节。以下是初次发现的历史快照，不代表当前仍有这些未解决差异。

harden-publication-retention 和 consolidate-shared-primitives-and-test-fidelity
任务已完成但仍未归档。download-registry-delivery baseline 的 max stale-while-revalidate
与 retention delta/现行 route 的 expire:0 不一致；旧 device/workflow cache 规范仍有
旧缓存形状。需要明确审查同步范围与验收，而非让文档清理自动改写产品契约。
见 docs/openspec.md。未来以 CLI 当前状态为准，不以本记录的“已完成”替代验收。

## 初次审查验证边界（历史快照）

本次是文档改动。验证针对本地 Markdown 链接、入口 import、当前 CLI 参数、
Requirement/Scenario 保持不变、git diff 空白检查和 OpenSpec strict validation。
验证结果：123 个现存 Markdown 文件的 84 个本地链接有效；8 个当前 IPA CLI
命令示例的参数与 argparse 定义匹配；CLAUDE import 符合要求；14 个 baseline 的
Requirements 区段逐字节不变；git diff --check 通过；OpenSpec strict validation
16 项通过、0 项失败。临时校验脚本位于 /tmp，不加入项目或新增产品工具。

未运行 Python/web 完整测试（没有产品代码改动），也没有真实 Apple 修改、私有签名、
R2/Vercel 发布、设备安装或 workflow dispatch。离线结构验证不能替代跨模型行为评测
或生产验收。

## 后续整改与归档（2026-10-04）

用户明确要求修复已指出的问题、归档变更并对齐当前文档/规范；另通过确认表单
授权补充用户级 profile 的 update 工作流和清理 4 个旧全局 Codex prompts。

- 重新审查当前 GitHub adapter、Apple snapshot/profile reconciliation、完整 signing
  fingerprint、pending index promotion、工作流触发与 cache-save 条件。
- 修正 device-list-caching、workflow-optimization、github-release-tracking 和
  scheduled-execution baseline：移除两个独立缓存文件、全局 rebuild_all、版本相同
  跳过全流程、direct URL 一律重签、force 必须重建 profiles 和固定 7 天缓存承诺。
  规范现描述完整输入身份、当前资源/profile 校验、独立验证、单一 signing index、
  成功后 promotion，以及缓存命中仍发布/revalidate/推进 retirement。
- signing-task-configuration、multi-bundle-signing 与 orchestration 清除过时迁移
  措辞；root-only 是当前统一引擎的配置默认，不是另一个兼容运行路径。
- 发现 dry sync 已在下层限制 Apple mutation，但编排层仍要求 apply 报告。先用
  ready/blocked 两个回归证明失败，再修正 AppleStage.sync 的只读分流。它只写 plan
  证据，不写 apply/sign/publish 或 cache-success；规范和 runbook 同步明确该契约。
- 删除重复 .codex skills、legacy openspec/AGENTS.md 与 openspec/project.md；能力路由
  转入 docs/specifications.md，有用架构事实保留在 config.yaml，历史 scoped 指引保留。
  CLI 已刷新 Claude/Codex 集成；CLAUDE.md 仍精确为 @AGENTS.md 加换行。
- profile 保留原 4 项并增加 update；已核对并移除 4 个旧全局 opsx prompts。
  再次 openspec update 确认工具均为 1.14.0 且无 legacy/missing-core 告警。
- 两项已完成变更通过 CLI 归档至 openspec/changes/archive/2026-10-04-*。
  归档前逐项比对所有 delta 要求；两次 CLI 均报告零规范修改。任务均完成，当前
  openspec list --json 无活动变更。第二项 proposal 的 Why 长度有非阻断提示，未为
  消除样式提示而改写历史提案。
- 将显式 HTML coverage 命令补入唯一开发模块，并更新文档契约测试的路由。
  首次完整回归因此暴露的旧 runbook 路径断言已修正，95% 门槛保持不变。

最终验证：916 passed、3 个 opt-in skipped，coverage 95.25%；Black/isort 通过，
strict mypy 113 个 package/script 文件通过；14 个 baseline strict validation 通过。
124 个受 Git 跟踪或未忽略的 Markdown、82 个本地链接和 8 个 IPA CLI 示例通过；
未涉及的 5 个 baseline Requirements 与 HEAD 相同；82 个已跟踪历史归档文件保持
原字节，新归档的 12 个原跟踪文件与移动前 HEAD 逐字节一致。uv build 成功生成
sdist/wheel；git diff --check 通过。

本轮有上述最小编排代码修复，但未运行真实 Apple 修改、私有签名、R2/Vercel 发布、
设备安装或 workflow dispatch；未修改历史验收、未提交。离线验证不替代生产验收。
