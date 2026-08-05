# 重构计划：消除重复代码 & 修复"仅测试使用"的生产代码

> **执行状态（2026-07-23）**：本文由 OpenSpec 变更 `consolidate-shared-primitives-and-test-fidelity` 全部落地，各问题与变更任务节的对应关系标注于下方标题。验收证据：全部 golden-value 摘要测试字节级不变、`uv run pytest`（含 95% 覆盖率门）通过、Black/isort/strict mypy 通过、架构守卫 `tests/test_production_stage_architecture.py` 通过、CLI `--help` 输出与基线逐字节一致。

> 本文档是对 `src/sideloadedipa` 全量代码结构分析的结论，用于指导 Agent 分阶段执行优化。
> 每个任务都给出：涉及文件、现状、目标、约束、验收方式。
>
> **全局约束（任何阶段都必须遵守）：**
> 1. `tests/test_production_stage_architecture.py` 是架构守卫：`pipeline/stages/*.py` 之间 import 必须无环、不得 import `pipeline.production`、不得出现 `from sideloadedipa.domain import`（必须用 `sideloadedipa.domain.<module>` 形式）。
> 2. **摘要稳定性**：`normalize_entitlements().sha256`、`policy_sha256()`、`stage_manifest_sha256()` 的输出是**持久缓存契约**（见 `pipeline/sign_stage.py` 中 `_SIGNING_POLICY_FINGERPRINT_INVARIANTS` 的注释）。任何规范化/摘要逻辑的重构必须保证**字节级输出不变**。动手前先补 golden-value 测试固定当前输出。
> 3. 错误类型/错误码（`ErrorCode`）是对外契约，重构时不得改变抛出路径上的 `code`、`remediation`、`safe_details`。

---

## 第一部分：重复冗余代码

### 问题 1.1 entitlement 键名常量在 5 处重复定义/硬编码 ✅（变更任务 §2）

**现状**（已核实）：

| 位置 | 形式 |
|---|---|
| `domain/entitlements.py:15-18` | `_APPLICATION_IDENTIFIER` / `_TEAM_IDENTIFIER` / `_KEYCHAIN_GROUPS` / `_APP_GROUPS` |
| `verification/entitlements.py:13-16` | 同四名常量重复定义 |
| `signing/profile_validation.py:29-30` | 重复定义前两个 |
| `apple/expected_entitlements.py:57-72` | 裸字符串字面量（含 `get-task-allow`、healthkit 系列） |
| `signing/planner.py:307`、`signing/inputs.py:90`、`tools/exercise_zsign_backend.py`（8 处） | 裸字符串字面量 |

**目标**：新建 `src/sideloadedipa/domain/entitlement_keys.py`，导出公共常量：

```python
APPLICATION_IDENTIFIER = "application-identifier"
TEAM_IDENTIFIER = "com.apple.developer.team-identifier"
KEYCHAIN_ACCESS_GROUPS = "keychain-access-groups"
APPLICATION_GROUPS = "com.apple.security.application-groups"
GET_TASK_ALLOW = "get-task-allow"
```

放在 `domain/` 是因为三个使用层（domain / signing / verification）都允许依赖 domain，且方向不反流。healthkit 等仅 `apple/expected_entitlements.py` 使用的键留在原地。

**步骤**：
1. 新建 `domain/entitlement_keys.py`（纯常量，无 import，避免环）。
2. 逐文件替换私有常量与字面量为共享常量的 import；删除旧的 `_APPLICATION_IDENTIFIER` 等。
3. **不**把它加入 `domain/__init__.py` 的 re-export（stages 架构测试禁止桶式 import；直接 `from sideloadedipa.domain.entitlement_keys import ...`）。

**验收**：`grep -rn '"application-identifier"' src` 仅剩 `entitlement_keys.py` 一处（plist 生成等真正需要字面量的除外，需逐个人工确认）；全部测试通过。

---

### 问题 1.2 "规范化 JSON + SHA-256" 有三套平行实现 ✅（变更任务 §1、§4）

**现状**：
- `domain/entitlements.py::normalize_entitlements`：`_canonical_value()` + `json.dumps(sort_keys, separators, allow_nan=False)` + sha256；
- `verification/entitlements.py::_digest`：`freeze_json` → `thaw_json` → `json.dumps(...)` + sha256；
- `util/atomics.py::canonical_json` + 至少 6 处内联 `hashlib.sha256(canonical_json(...)).hexdigest()`：
  `pipeline/sign_stage.py:29`、`pipeline/stage_manifests.py:55`、`pipeline/input_manifests.py:274,311`、`pipeline/run_reports.py:231`、`tools/qualify_backend.py:108`。

**风险**：`canonical_json` 没有 `allow_nan=False`，而 domain 版有——NaN 处理不一致，同一文档在不同路径可能得到不同摘要。

**目标**：
1. 在 `util/atomics.py` 增加 `json_sha256(document, *, default=None) -> str`，内部用 `canonical_json`，并统一 `allow_nan=False`（先用 golden 测试确认现有语料中没有 NaN/Inf，保证摘要不变）。
2. 替换上述 6 处内联摘要。
3. `domain/entitlements._canonical_value` 与 `verification/entitlements._digest` 保留各自的语义入口，但序列化统一委托给同一底层函数（可放 `domain/common.py`，`util/atomics.py` 已依赖 domain，方向可行）。

**验收**：动手前先用脚本对 `tests/fixtures/**` 中所有 entitlement/manifest 文档记录当前摘要；重构后逐一比对不变。

---

### 问题 1.3 `domain/entitlements._freeze` 重复 `domain.common.freeze_json` ✅（变更任务 §3.3）

**现状**：`domain/entitlements.py:66-80` 的 `_freeze` 与 `domain/common.py::freeze_json` 逻辑同构，唯一差别是出错时抛 `DomainError(ENTITLEMENTS_POLICY_INVALID)` 而非 `TypeError`。

**目标**：删除 `_freeze`，改为 `try: freeze_json(v) except TypeError: raise _policy_error(...)`。`_canonical_value` 同理评估能否用 `thaw_json(freeze_json(...))` 替代（注意它还要按键排序——`freeze_json` 已排序，可行）。

**验收**：`tests/test_entitlement_policy.py`、`tests/test_domain_models.py` 通过；错误码不变。

---

### 问题 1.4 字符串数组校验器三份 ✅（变更任务 §3.2）

**现状**：
- `domain/entitlements.py::_string_array`（抛 DomainError）
- `signing/profile_validation.py::_string_list`（抛 `_invalid_profile`）
- `signing/inputs.py::_strings`（抛 `_profile_error`，且额外拒绝空字符串）

**目标**：在 `domain/common.py` 增加谓词 `is_string_sequence(value, *, allow_empty=True) -> bool`；三处保留各自的错误映射，只替换类型判断样板。

---

### 问题 1.5 `{key: thaw_json(v) for key, value in ...}` 重复 12 次 / 10 个文件 ✅（变更任务 §3.1）

**现状**（已核实）：`pipeline/stages/results.py:11`、`signing/planner.py:63,199,305,318`、`signing/service.py:110-111`、`signing/profile_validation.py:320`、`cache/fingerprint.py:96`、`util/atomics.py:123`、`adapters/signing/zsign.py:112,248`、`verification/three_way.py:32`（已局部封装为 `_document`）、`cli.py:85`。

**目标**：在 `domain/common.py` 增加：

```python
def thaw_json_object(values: Iterable[tuple[str, FrozenJsonValue]]) -> dict[str, object]:
    return {key: thaw_json(value) for key, value in values}
```

全量替换；`three_way._document` 改为别名调用。

---

### 问题 1.6 `pipeline/sign_stage.py` 与 `pipeline/stages/signing.py` 分层收拢 ✅（变更任务 §7）

**现状**：`pipeline/sign_stage.py`（fingerprint 构建、template 摘要、缓存报告恢复）**仅**被 `pipeline/stages/signing.py` import；`pipeline/publish_stage.py` 同样仅被 `stages/publication.py` 使用。这是旧扁平层与新 stages 层并存的残留。另外 `pipeline/publication.py` / `pipeline/publish_stage.py` / `pipeline/stages/publication.py` 三名极易混淆。

**目标**（保守做法，避免一次大迁移）：
1. 把 `sign_stage.py` 的 4 个函数（`json_digest`、`template_digests`、`build_fingerprint`、`policy_sha256`、`device_set_sha256`、`restore_cached_signing_report`）迁入 `pipeline/stages/signing.py`；若文件超过 ~500 行，拆出 `pipeline/stages/signing_cache.py`（stages 内 import 必须保持无环，`signing_cache` 不得 import `signing`）。
2. `publish_stage.py` 同理并入 `stages/publication.py`。
3. 重命名 `pipeline/publication.py` 为 `pipeline/publication_service.py`（它是 `VerifiedPublicationService` 的领域服务，不是 stage）。
4. 注意 `tools/qualify_backend.py:28` import 了 `pipeline.sign_stage.policy_sha256`——迁移时同步更新。
5. 迁移后删除空文件，更新所有 import。

**验收**：`test_production_stage_architecture.py` 通过；`grep -rn "sign_stage\|publish_stage" src` 无残留。

---

### 问题 1.7 小型重复（一并处理） ✅（变更任务 §3.4）

- `signing/profile_validation.py::validate_entitlement_authorization` 是 `validate_expected_entitlements` 的一行包装，仅被本文件调用一次 → 内联删除，或反过来让 321 行直接调 `validate_expected_entitlements`。
- `verification/profiles.py::_digest` 就是 `file_sha256` 的别名 → 删除。

---

## 第二部分：仅被测试使用、与生产脱节的代码

### 问题 2.1 三个"生产路径零调用、仅测试调用"的公开函数（已核实） ✅（变更任务 §5.1–5.3、§5.5）

| 函数 | 位置 | 生产调用方 | 测试 |
|---|---|---|---|
| `execute_after_preflight` | `signing/preflight.py:127` | 无 | `tests/test_preflight.py` |
| `referenced_keys_from_apps` | `adapters/publication/r2_store.py:285` | 无 | `tests/test_r2_store.py` |
| `skip_stage` | `pipeline/stage_manifests.py:250` | 无 | `tests/test_stage_manifests.py` |

这三个函数是"为测试而写"或"接线时被绕过"的死代码——测试在验证一段生产永远不执行的逻辑，未来改动时测试通过但生产行为可能已偏离。

**处理策略（逐个决策，不要一刀切删除）：**
1. `execute_after_preflight`：逻辑平凡（`if not result.valid: return False`），生产在 `stages/source_inventory.py` 自行实现了同等判断。**删除函数与对应测试**，或（若 intended as extension point）在 docstring 标注并纳入 `__all__`。推荐删除。
2. `referenced_keys_from_apps`：看起来是 R2 孤儿键 GC 的预留。决策：若无计划中的 GC 功能 → 删除函数与测试；若 roadmap 需要 → 移到 `tools/` 并标注未接线。
3. `skip_stage`：stage manifest 跳过策略。检查 `stages/*` 是否都应走 `record_success` 而没有跳过路径——若确认生产无跳过语义，删除；若有（如 force rebuild 短路），则**把生产接线补上**而不是删。

**验收**：`grep -rn "<name>" src` 在删除后无残留；或接线后生产路径有真实调用且测试改为通过生产入口间接触达。

---

### 问题 2.2 生产管线测试使用替身后端，与真实 Zsign 后端脱节 ✅（变更任务 §6）

**现状**：`tests/conftest.py::FixtureCopyBackend`（复制文件冒充签名）与 `FixturePassingVerifier`（无条件全部通过）被 `tests/test_production_pipeline.py`、`tests/test_pipeline_failure_injection.py` 使用。即"生产管线"测试从未触及真实 `adapters/signing/zsign.py::ZsignBackend` 与 `verification/service.py::PackageVerifier` 的接线。真实后端仅靠手动的 `sideloadedipa-qualify-backend` 入口（`tools/qualify_backend.py`）和 `tests/test_exercise_zsign_backend.py` 覆盖。

**风险**：`ports.SigningBackend` 协议或 `PackageSigningRequest` 字段变化时，替身不同步，管线测试依然全绿。

**目标（按优先级）：**
1. **契约测试**：新增 `tests/test_backend_contract.py`，对 `FixtureCopyBackend` 与 `ZsignBackend`（构造即可，不执行签名）断言同样满足 `ports.SigningBackend` 协议（`runtime_checkable` Protocol 或显式签名检查），替身与真实实现共享同一份协议符合性测试。`FixturePassingVerifier` 与 `PackageVerifier` 同理。
2. **替身集中化**：把两个 Fixture 类从 `conftest.py` 移到 `tests/fakes.py`，并在模块 docstring 写明"必须与 ports.py 协议同步，由 test_backend_contract.py 强制"。
3. （可选，成本较高）增加 `@pytest.mark.integration` 测试，在 macOS/有 zsign 的环境用真实后端跑一条端到端路径，CI 中 allow-skip。

**验收**：协议变更时契约测试必然失败；`grep -rn "FixtureCopyBackend" tests` 仅指向 `tests/fakes.py`。

---

### 问题 2.3 `tools/` 包的定位澄清（记录即可，无需改代码） ✅（变更任务 §5.4）

`tools/qualify_backend.py` 及其子模块（`build_backend_qualification_fixture`、`compare_backend_qualification`、`exercise_codesign_oracle`、`exercise_zsign_backend`）没有生产 import 方，但它是 `pyproject.toml` 注册的 console script（`sideloadedipa-qualify-backend`），属于开发者工具而非死代码。**不需要移动**，但建议在 `tools/__init__.py` docstring 注明"手动资格认定工具，不在自动管线内；其覆盖的后端行为与 2.2 的契约测试互补"。

另外清理残留：`scripts/__pycache__/` 下有已迁移旧脚本（`qualify_backend_prerequisites`、`exercise_zsign_backend`）的 `.pyc` 缓存，删除 `scripts/__pycache__`。

---

## 执行顺序建议

| 阶段 | 内容 | 风险 | 依赖 |
|---|---|---|---|
| P0 | 补摘要 golden-value 测试（1.2 的前置） | 无 | — |
| P1 | 1.1 常量、1.3 freeze、1.4 字符串数组、1.5 thaw helper、1.7 小清理 | 低，纯机械替换 | — |
| P2 | 1.2 摘要统一 | 中（缓存契约） | P0 |
| P3 | 2.1 死代码三处决策与处理 | 低 | — |
| P4 | 2.2 契约测试 + 替身集中化 | 低（只动 tests/） | — |
| P5 | 1.6 stage 分层收拢 | 中（import 图变动） | P1（减少迁移时的冲突面） |

每个阶段独立成 commit / PR，完成后运行：

```bash
uv run pytest -x -q
uv run ruff check src tests   # 如项目配置了 ruff/mypy 则一并运行
```
