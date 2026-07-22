# 分支审查与改进计划：`feat/add-multi-bundle-ipa-signing`

> 审查日期：2026-07-22
> 审查范围：`master...feat/add-multi-bundle-ipa-signing`（255 个文件，约 +40k / -4.6k 行）
> 测试基线：722 通过、2 跳过、8.9 秒；覆盖率 95.07%（门槛 95%）

## 总体判断

本分支将脚本式工具（`scripts/run_signing.py` 等）重写为 `src/sideloadedipa` 包，方向正确，整体质量高于大型重写的常见水平：

- 错误分类学设计良好（49 个稳定错误码 + 结构化诊断 + 补救提示）；
- 子进程封装扎实（强制超时、输出封顶、环境白名单、argv/输出脱敏、`shell=False`）；
- 测试底层保真度出色（手工构造的真实 Mach-O、真实 openssl CMS 签名、真实 zip 攻击样本）。

但存在四类系统性问题，以及 6 个计划外的安全/正确性问题（见第四节，优先级最高）：

1. **"legacy" 名不副实**——发布路径上的两个生产关键模块（`r2_store`、`app_icon`）藏在被 mypy strict 和覆盖率门槛同时豁免的 `legacy/` 包里；
2. **迁移半途的平行路径**——一个只被自己的测试和 CI fixture 供养的孤儿编排引擎（`pipeline_application.py`），以及一整层被 CLI 绕过的死命令代码（`package_commands.sign_command`/`run_command` 等）；
3. **测试保真度倒挂**——底层用真实字节，顶层全是 fake：打过补丁的 zsign 二进制从未被任何测试执行，唯一的真实 IPA 集成测试从未在任何 CI 中运行；
4. **顶层模块爆炸与重复**——35 个松散顶层模块；`_canonical_json` 重复实现 8 次、原子写 5 次、文件 SHA-256 3 次、诊断序列化 6 次。

---

## 一、测试审查

### 结论：真正的问题不是冗余，而是"金字塔倒挂"

凑覆盖率的测试约 250–300 行，远少于 95% 门槛暗示的量；真正的风险是**集成层全部跑在 fake 上，而 fake 锁定的假设无人验证**。

### 1.1 该删的凑数测试（约 300 行）

| 位置 | 问题 |
|---|---|
| `tests/test_ports.py`（整文件，58 行） | 对 `@runtime_checkable` 协议做 `isinstance` 断言只检查方法名存在，零回归价值；纯粹为 `ports.py` 的 `...` 方法体刷覆盖 |
| `tests/test_domain_models.py`（3 删 2） | 保留全量 frozen 扫描 `test_every_domain_dataclass_is_frozen`；删除对单个类重复证明 frozen（:45-81）和断言 dataclass 默认字段值（:83-87）的两个 |
| `tests/test_errors.py:47` | `test_error_categories_remain_distinct` 断言两个不同的类不是同一个类，恒真 |
| `tests/test_production_pipeline.py:692` | 委托镜像测试（证明 `inspect_command` 调用 `_execute_default(…, "inspect")`），纯粹复述七个单行函数 |
| `tests/test_production_pipeline.py:562` | 深入私有 helper（`_source_asset`、`_published_at("invalid")` 等）点亮错误分支；把其中"未知任务名抛 `ConfigurationError`"一条并入现有行为测试后删除 |
| `tests/test_legacy_characterization.py:32,51` | 一个把 fixture 内容逐字节复述进测试（fixture 与测试同步漂移，互相验证失效）；一个测试自己测试文件里定义的 helper |
| `tests/test_workflow_toolchain.py:24-26,173-180` | 版本墓碑断言（"旧版本号不在文件里"）——永久性 tombstone，每次例行 action 升级还会误报。**保留** :78-159 的密钥作用域断言（SSH 调试步骤不可见签名密钥） |
| `tests/test_signing_service.py:205-227` | 5 路参数化只变化任务名，任务形状已由 `test_config_parser.py` 锁定；收缩到 1–2 个代表用例 |

### 1.2 冗余簇（合并而非删除）

- `tests/test_stage_store.py`：文件名过时（不存在 `stage_store` 模块，实际测 `manifest_store.FileStageManifestStore`），3 个测试与 `test_manifest_store.py` 的 7 个重叠 → 并入后者；
- `tests/test_livecontainer_verification_contract.py`（337 行）：是 `verify_three_way_entitlements` 通用性质的生产值实例化 → 收缩到敏感键用例（`SENSITIVE_KEYS`）约 180 行，或折叠为 `test_three_way_entitlements.py` 的一个参数化块；
- legacy 资格认证脚本测试簇（约 800 行，6 个文件）→ 约 500 行：合并 `test_exercise_zsign_backend.py` + `test_exercise_codesign_oracle.py`（后者一半主题从前者 import）；保留守护破坏性 ASC 操作的测试（如 `test_delete_legacy_bundle_ids_requires_exact_legacy_name`）；
- **测试间横向 import 解耦**：`test_pipeline_failure_injection.py:24-25` 从另外两个测试模块 import fixture，`test_production_pipeline.py:43` 又从第三个 import——删改任何一个连坏两个。把 `CopyBackend`、`request_for`、`source_context`、`dependencies`、`command` 提入 `conftest.py`；
- **conftest 工厂函数**：11 个文件各自手搓 `SigningPlan(...)`、11 个手搓 `ProvisioningProfile(...)`（位置参数 `"a"*64` 风格），给 `SigningPlan` 加一个字段要改 11 处 → 补 `plan_factory()` / `profile_factory()`，消除约 300 行 fixture 噪音。

### 1.3 保真度缺口（按风险排序；修法所需组件全在库里，只差接线）

| # | 缺口 | 风险 | 修法 |
|---|---|---|---|
| 1 | **补丁版 zsign 从未被测试执行**。`test_zsign_backend.py:38-61` 用 Python 脚本 fake zsign 并锁定 argv 约定（`-m`/`-e` 相邻配对、无 `-p`、`[output, source]` 尾序）；生产跑的是自编译 `1.1.1+sideloadedipa.2`（`adapters/signing/zsign.py:25`）。若补丁参数语义漂移（如 `-e` 绑定到后一个而非前一个 `-m`），所有测试通过，生产把错误的 entitlements 签进错误的 bundle。唯一真实执行是 post-merge 的 backend-qualification canary，不在 PR CI。PR CI 甚至下载并校验了真实 zsign 然后从未使用 | 高 | 加 `ZSIGN_BIN` 门控的 pytest：用 `scripts/build_backend_qualification_fixture.py` 构建确定性 4-bundle fixture IPA + 复用 `test_profile_validation.py:33-139` 的自签证书/openssl-CMS profile 生成，跑真实 `ZsignBackend.sign()`，再用真实 `inspect_signed_entitlements` + `verify_signed_profiles` 验证输出；在 pr-checks 的 python-tests job 导出 `ZSIGN_BIN` |
| 2 | **服务层的"验证通过"是恒真式**。`PassingVerifier`（`test_signing_service.py:116-124`）对每个必查项伪造通过、`CopyBackend` 原样拷贝——产出密码学垃圾的后端也能在所有 pytest 层通过 sign→verify→publish。真实 verifier 逻辑单测充分，但从未被组装进 sign→verify 流 | 高 | 一个组装测试：复用 `test_signature_verification.py` 的 `macho()`/`superblob()`/`code_directory()` 构造器让后端产出真实最小签名，接真实 `PackageVerifier`；配负例（未签名可执行 → verify 失败且 artifact 不晋升） |
| 3 | **唯一的真实 IPA 测试从未运行**。`test_livecontainer_integration.py`（校验和固定的 LiveContainer 3.8.0 下载 + 真实解包 + 真实 `discover_bundle_graph`，含 LiveWidget 缺签名负例）门控在 `SIDELOADEDIPA_RUN_LIVECONTAINER_INTEGRATION=1` 上，无任何 workflow 设置它；`integration` marker 在 pyproject 里纯属声明 | 高 | 在 sign-and-upload 增加步骤或每周定时 job 设置该变量跑 `pytest -m integration --no-cov`（资产可按校验和缓存），零新代码 |
| 4 | bundle-graph 发现全程用 `MarkerMachOProbe`（`startswith(b"MACHO")`，`test_bundle_graph_discovery.py:23-44`）；真实探针误判（fat 二进制、嗅探成 Mach-O 的资源文件、加密 slice）会改变图形状 → 签名顺序 → profile 分配 | 中 | 把 `test_entitlement_inspector.py:55-73` 的真实 thin Mach-O 构造 helper（`make_thin`/`struct.pack`）挪到 conftest，2–3 个发现用例改用默认（真实）协作者 |
| 5 | R2 发布层测试全是 `MagicMock` kwargs 断言（`test_r2_store.py`）；boto3 漂移或错误的 `ExtraArgs` 键只在生产发布时爆 | 中 | 换 `botocore.Stubber`（无新依赖），请求形状对真实 S3 service model 校验；重点覆盖 stale-key 删除与 registry 往返 |

### 1.4 关于 95% 覆盖率门槛

门槛目前的实际作用是**供养死代码**——死代码靠自己的测试维持覆盖，删测试就掉出门槛，形成互锁。按第二节删除死代码及其测试后覆盖率会不降反升，门槛本身可保留。

### 1.5 保持原样的高质量测试（不要动）

`test_entitlement_inspector.py`（金标准：手工组装 thin/fat Mach-O + superblob + 真实 codesign 捕获的 DER）、`test_signature_verification.py`（真实 CodeDirectory 4KB 页哈希 + 真实 PKCS7/CMS + cdhash 签名属性）、`test_profile_validation.py`（真实证书 + `openssl cms -sign`，16 路突变参数化拒绝，UDID 脱敏断言）、`test_pipeline_failure_injection.py`（每个 `PipelineStage` 的失败注入矩阵）、`test_production_pipeline.py:304-394`（缓存命中/篡改重签）、`test_safe_archive.py`（zip-slip 等真实攻击样本）、`test_subprocesses.py`（真实进程注入/超时/脱敏）、`test_stage_manifests.py` + `test_manifest_store.py`（哈希链防篡改）、`test_config_parser.py:34-82`（锁定真实生产 `configs/tasks.toml`）、`test_output_integrity.py` / `test_signed_entitlement_evidence.py` / `test_signed_profile_verification.py`。

---

## 二、精简：遗留与死代码

### 2.1 立即可删（纯减法，无行为变化）

| 目标 | 证据 | 附带删除 |
|---|---|---|
| **孤儿编排引擎闭环**：`src/sideloadedipa/pipeline_application.py`（270 行） | 生产零消费：CLI 全部直连 `production_pipeline`（`cli.py:23-43`），后者自带语义更强（幂等重放校验）的手写 manifest 编排（`production_pipeline.py:411-460`）；消费者仅 `scripts/run_workflow_fixture.py` 与自己的测试 | `scripts/run_workflow_fixture.py`、`tests/test_pipeline_application.py`、pr-checks 与 sign-and-upload 中的 "workflow fixture" 步骤。备选方案：让 `ProductionPipeline` 真正跑在该引擎上（属阶段 2 重构，非精简；推荐删除） |
| **`package_commands` 死命令层**（约 200 行） | `sign_command:276`、`run_command:301` 及私有闭包（`_sign_tasks`、`_task_report`、`_SignedTask`、`_redirect_progress`、`_release_cache_entry`、`_update_release_cache` 等）被 CLI 完全绕过；唯一调用者 `tests/test_package_commands.py` | 对应死测试（约 200 行）。保留 `_trigger_revalidation` / `_publication_runtime` 相关测试（经 R2 网关回调仍活跃）。把"publication 禁用的任务在签名前被拒"不变量改挂到 `ProductionPipeline` 测试 |
| `package_runner.run_package_signing:112` | 仅被死的 `package_commands._sign_tasks` 与自测调用（`inspect_source_graph`、`prepare_package_signing` 仍活跃） | `test_package_runner.py` 中对应测试；同文件 :101-184 的六协作者 monkeypatch 镜像测试减半 |
| `inspection.inspect_command:283` 及闭包（`_selected_tasks`、`_diagnostic`、`_structure_document`、`_inspect_task`） | 仅 `test_inspection.py` 消费（`resolve_source`、`ResolvedSource`、`InspectDependencies` 仍活跃） | 对应测试 |
| **`ports.py` 7 个死协议**：`SourceRepository`、`ArchiveInspector`、`AppleDeveloperClient`、`CertificateProvider`、`ArtifactStore`、`RegistryPublisher`、`Filesystem` | `src/` 与 `scripts/` 零引用（已亲自 grep 证实）；保留 `SigningBackend`、`Verifier`、`Clock`、`VerifiedPublicationGateway` | `tests/test_ports.py` 整文件 |
| `legacy/sync_profiles_asc.py`（458 行） | 功能已被 `sideloadedipa sync`（`apple_commands.sync_command` + `adapters/apple/{state,profiles,asc}.py`）完整重写；消费者仅测试与 README 过时描述 | `scripts/sync_profiles_asc.py`、`tests/test_sync_profiles_asc.py`；从 `test_legacy_characterization.py` 抢救 3 个非 sync 测试（livecontainer-3.8.0 校验和固定、漂移、release audit——支撑 canary 的 checksum pin）到独立 release-audit 测试文件，其余 5 个删除 |
| `legacy/reconcile_icons.py`（130 行） | 一次性运维工具；自己的 docstring 说明两次图标策略迁移均已完成，例行清理由生产管线 `cleanup_stale` 承担 | `scripts/reconcile_icons.py`、`tests/test_reconcile_icons.py` |
| 零消费散件 | `domain/pipeline.py:35 StageState`（re-export 后零引用）；`retrying.reconcile_additive_once:68`；8 个仅自测消费的序列化函数：`cache_fingerprint.canonical_cache_fingerprint_json`、`signing_planner.canonical_signing_plan_json`、`signing_reports.canonical_signing_result_json`、`run_reports.human_run_report`、`verification/report.canonical_verification_report_json` + `human_verification_report`、`adapters/apple/state.canonical_apple_snapshot_json` | 各自对应测试段落 |
| 无 CI 消费者的 `scripts/` delegator | `scripts/{app_icon,r2_store,sync_profiles_asc,reconcile_icons}.py`（CI 只执行 `run_workflow_fixture` + 5 个 qualification delegator） | `tests/test_legacy_delegators.py` 的 `LEGACY_MODULES` 清单同步收缩 |
| `scripts/benchmark_pipeline.py` | 无 workflow/文档引用，仅为已归档 openspec 证据生成过数据；其 `sys.path.insert` 在 src-layout 下是 no-op（实际依赖 venv 安装的包） | `tests/test_benchmark_pipeline.py`。或者：正式接入 CI，二选一，不要悬着 |
| 死 fixture | `tests/fixtures/baseline/compatibility-contract.json`（记录已删除的 `run_signing`/`sync_profiles_asc` 契约，零测试加载） | — |

### 2.2 需要先迁移再删（转正，不是删除）

- **`legacy/r2_store.py`（302 行）不是 legacy**：唯一的 R2 实现——`adapters/publication/r2.py:15` 的 `R2PublicationGateway` 只是它的重试包装；`production_pipeline.py:74` 经 `_publication_runtime` 在发布路径使用。
- **`legacy/app_icon.py`（391 行）不是 legacy**：`production_pipeline.py:70` 与 `package_commands.py:37` 直接 import，发布阶段调用（`production_pipeline.py:1137`）。
- 两者目前**同时豁免于 strict mypy（pyproject.toml:56）和 95% 覆盖门槛（pyproject.toml:93）**——生产发布路径是全库类型与覆盖检查最弱的部分。迁入 `adapters/publication/`（`r2_store.py`、`icons.py`），纳入两个门槛，更新 import 与测试路径、README。
- **qualification 五件套**（`build_backend_qualification_fixture`、`qualify_backend_prerequisites`、`exercise_zsign_backend`、`exercise_codesign_oracle`、`compare_backend_qualification`，约 1900 行）有真实 CI 消费者（sign-and-upload 的 backend-qualification 三个 job）。作为一个整体迁出 legacy（`tools/` 子包或 CLI 子命令，纳入 mypy），之后重定向 CI 引用，`scripts/_bootstrap.py` 与整个 delegator 机制随之退役。注意 `exercise_zsign_backend` 已是半迁移混合体（内部 import 新包的 `apple_intents`/`config`/`domain`/`profile_validation`）。

### 2.3 文档漂移（8 处）

1. `MIGRATION.md:85,92,95,161,199` 仍把已删除的 `scripts/run_signing.py` 当现役编排器；:192 引用已删除的 `apps_registry.py`；未记录本次 scripts→package 迁移本身；
2. `pr-checks.yml:108-110` 注释以已删除的 `check_changes.py` 为 mypy scripts 非阻塞的理由（理由已不存在，该步骤应转阻塞）；
3. `README.md:17,19` 文件结构清单把 `scripts/sync_profiles_asc.py`、`scripts/r2_store.py` 描述为现行机制；
4. `README.md:229,262` 称 zsign 用"官方预编译 Linux 二进制"——生产实际用源码编译补丁版 `1.1.1+sideloadedipa.2`；
5. `README.md:210-215` 触发器清单漏掉 `backend_qualification`、`apple_state_probe`、`qualification_apply`、`qualification_reset_names` 四个 dispatch 输入（runbook 同样缺失）；
6. `README.md` 密钥清单漏 `Instatus_Webhook_URL`（缺失时 Notify 步骤在 `set -euo pipefail` 下把成功运行标记为失败）与 `VERCEL_REVALIDATE_URL` 仓库变量；`R2_REGION` 列为 secret 实为普通 env 字面量；
7. `docs/security.md` 称"CI artifacts 不含 IPA 与私有材料"——与 canary job 上传签名 IPA artifact 矛盾（见 4.5）；
8. `README.md:320` "Run tests (when available)"——已有约 100 个测试文件。

---

## 三、优化：架构、目录结构与健壮性

### 3.1 目录重组：35 个顶层模块 → 5 个

绝大部分是机械 `git mv` + import 改写（测试 import 面大但纯文本改动）：

```
src/sideloadedipa/
├── __init__.py / __main__.py
├── cli.py / application.py / errors.py / ports.py   # 仅保留这些顶层（ports 裁剪至 4 个活协议）
├── domain/  config/  ipa/  sources/  verification/   # 不动
├── adapters/
│   ├── apple/        (+ backend.py ← apple_commands 中的 AppleCommandBackend/AscAppleCommandBackend)
│   ├── signing/      # 不动
│   └── publication/  (+ r2_store.py、icons.py ← 从 legacy 转正)
├── util/             # NEW：subprocesses、retrying、workspace、atomics(新建)
├── apple/            # NEW：intents、planning、state_probe、commands
├── signing/          # NEW：order、planner、executor、service、inputs、reports、
│                     #      bundle_transform、certificate_identity、preflight、
│                     #      profile_storage、profile_validation
├── cache/            # NEW：fingerprint、decisions、reuse、store
├── pipeline/         # NEW：production(← production_pipeline)、engine(← 若保留 pipeline_application)、
│                     #      stage_manifests、manifest_store、environment(新建)、inspection、
│                     #      publication、package_runner、run_reports、cancellation
└── legacy/           # 收缩后仅剩真正可忽略的部分，最终清空
```

外部引用约束：`sign-and-upload.yml:456` 的 `python -m sideloadedipa.apple_state_probe`（留 shim 或同步改 workflow）；pyproject 的 `sideloadedipa.cli:main`；CI 缓存键哈希 `src/sideloadedipa/**`（不受影响）。

### 3.2 分层违规修复（4 处）

1. **`verification` ⇄ `profile_validation` 双向依赖**：`verification/profiles.py:23` import 顶层 `profile_validation`，后者 :22 反向 import `verification`；目前只靠 `verification/__init__.py` 刻意不导出 `profiles`/`service` 才没形成运行时环，往 `__init__` 加任何一个即 ImportError。修法：`profile_validation` 直接依赖叶子模块 `verification/entitlements.py`；
2. `adapters/apple/profiles.py:22` 适配器向上引用服务层 `profile_validation`；
3. `production_pipeline.py:72-78` 跨模块 import 四个下划线私有 helper（`_decode_p12`、`_publication_runtime`、`_required`、`_safe_filename`）→ 提取公共 `pipeline/environment.py`（连同三处重复的 `_selected_tasks`），同时消解 `package_commands.py` 存在的大部分理由；
4. 生产代码依赖 `legacy/`（2.2 转正即解）。

次要：`production_pipeline.py:18` 为报告分类 import `adapters.apple.capability_rule`（策略表应下沉 domain）；`apple_commands.py:361` 无必要的函数内延迟 import。

### 3.3 神模块拆分

- **`production_pipeline.py`（1269 行、52 个定义，约 9 种职责）**：依赖容器 + env 校验 / source 下载持久化 / 手写 manifest 记账 / inspect+preflight / Apple 委托 / `sign()` 内整个缓存生命周期（约 150 行最密的结：指纹→重建决策→重验证回退→缓存报告恢复→晋升）/ verify+publish（图标上传与发布候选块与 `package_commands.run_command` 几乎逐字重复）/ 运行报告组装 + 脱敏 / 底层原子文件工具。自然切分：`pipeline/source_state.py`、`pipeline/sign_stage.py`、`pipeline/publish_stage.py` + 共享 `util/atomics.py`，留一个薄的顺序器类；
- **`apple_commands.py`（776 行，5 种职责）**：后端协议 + 具体 ASC 后端（74-203 行，归 adapters 侧）/ 期望 entitlement 推导（247-316，纯策略）/ 规划需求构建 / 报告渲染 / plan+sync 命令（含 110 行多轮 apply 循环）。拆 `apple/backend.py`、`apple/expected_entitlements.py`、`apple/reporting.py`、`apple/commands.py`，除 entitlement 推导外均为机械操作。

### 3.4 重复消除（机械、收益最高）

| 重复项 | 位置 | 收敛到 |
|---|---|---|
| `_canonical_json` ×8 | cache_fingerprint:39、cache_decisions:71、profile_storage:55、stage_manifests:67、run_reports:209、signing_reports:21、ipa/graph:245、verification/report:144 | `util/` 或 domain 单一实现 |
| 原子写（tempfile+fsync+replace）×5 + 2 个无 fsync 变体（持久性保证不一致） | cancellation:40、cache_store、manifest_store、run_reports:244、production_pipeline:254；无 fsync：production_pipeline:244,306、package_commands:201 | `util/atomics.py` |
| 文件 SHA-256 ×3 | package_commands:118、production_pipeline:132、cache_reuse:35 | `util/atomics.py` |
| `Diagnostic`→dict ×6 | cli:98、pipeline_application:78、stage_manifests:38、signing_reports:25、verification/report:121、apple_commands:423 | domain 层序列化器 |
| 脱敏算法 ×2 | subprocesses:35 `_redact`、run_reports:51 `_redact_text` | `util/` |
| `_selected_tasks` ×3（校验质量漂移：一处区分重复/未知任务名，两处合并单一消息） | apple_commands:206、package_commands:76、production_pipeline:182 | `pipeline/environment.py` |

三种报告文档（run/signing/verification）本身合理区分，重复只在管道层，无需合并 schema。

### 3.5 健壮性

- **`retrying` 半悬**：仅 R2 网关使用；ASC 调解器裸调无重试（尽管 `APPLE_RATE_LIMITED` 错误码存在、`reconcile_additive_once` 专为 ASC additive-create 竞态设计却零使用者）；下载路径（`sources/`）同样无重试。要么用起来要么删（本计划：删 `reconcile_additive_once`，ASC/下载重试作为独立小改进评估）；
- **异常分类学泄漏**：`run_reports.py` 7 个裸 `ValueError`（:75,163-174,220-222）+ `verification/report.py:219,223`、6 个 CLI 可达的 `TypeError` 守卫（apple_commands:547、package_commands:294,383、pipeline_application:74,230、production_pipeline:153、inspection:326）会绕过 `SideloadedIPAError` 处理器以裸 traceback 逃逸——恰是结构化诊断最有价值的不变量违规路径。收敛为 `DomainError` 或内部错误码；
- `subprocesses.py`：输出封顶只留尾部 64 KiB 而工具错误横幅通常在头部（改为头尾各留）；`timeout_seconds=0` 经 falsy-`or` 静默变默认值（:95）；
- `cancellation` journal 只写不读（无人消费它调解孤儿 Apple 资源）——明确"纯证据"定位写入文档，或补消费者；SIGTERM 缺口见 4.4；
- `sources/download.py` 无最大体积上限、无 URL scheme 白名单（配置可控，低风险，顺手加）；
- 两种时钟约定并存：`ports.Clock` vs `Callable[[], datetime]`（`ProductionPipelineDependencies.clock`），统一为一种。

---

## 四、计划外发现：安全与正确性（优先级最高）

1. **工作流派发落穿（已证实）**：生产 job 门控（sign-and-upload.yml:62-65）只排除 4 个模式布尔值；操作者单独勾选 `qualification_apply` 或 `qualification_reset_names` 派发时**静默执行完整生产发布**而非资格认证。加入排除条件或前置校验步骤；
2. **revalidation 密钥进 URL 查询串（已证实）**：`package_commands.py:131-138` 把 `VERCEL_REVALIDATE_SECRET` 拼进 GET 查询串，会落入 Vercel 请求日志与任何中间层。改 header 或 POST body（需同步改 web 端点）；
3. **`publication_enabled` 默认 fail-open（已证实）**：`config/parser.py:295` 默认 `True`；省略该键的新任务立即可发布，与文档（`configs/tasks.toml.example:119`、README）承诺的"设备验收前保持 false"相反。默认改 `False`，现有 `configs/tasks.toml` 各任务显式声明；
4. **取消日志不覆盖 SIGTERM**：`cancellation.py:98-104` 只捕获 `KeyboardInterrupt`/`CancelledError`；GitHub Actions 取消升级到 SIGTERM 时不写 journal——恰是最需要它的场景。注册 SIGTERM 处理器转换为可捕获异常；
5. **签名 canary IPA 作为 artifact 上传**（sign-and-upload.yml:697-704，保留 1 天）：嵌入 profile 含设备 UDID，任何有 repo Actions 读权限者可下载；与 `docs/security.md` 承诺直接矛盾。二选一：停止上传（qualification 证据已足够），或修订 security.md 并明示风险；
6. **SSH 调试会话期间 `GITHUB_TOKEN` 留存**：`actions/checkout` 默认 `persist-credentials: true`，token 存于 `.git/config`，现有环境变量清理不覆盖它；两个 workflow 的所有 checkout 加 `persist-credentials: false`；
7. 次要：macOS job `security import -P "$P12_PASSWORD"`（:775-776）把真实证书密码暴露在进程表（Linux 侧已正确用 `-passin env:`；macOS `security` 无 env 传参，可评估先用 openssl 解出 PEM 再导入，权衡落盘风险）；qualification 负控制结果（`zsign-profile-only-summary.json`）上传后无人断言——上游 zsign 意外通过或崩溃时运行仍为绿色，comparison job 应消费它（`compare_backend_qualification.py:62-67` 已有现成校验路径）；报告脱敏 token 清单（production_pipeline:1030-1035）不匹配 `R2_ACCESS_KEY_ID`/`ASC_KEY_ID`/`ASC_ISSUER_ID`（纵深防御缺口）。

---

## 五、CI 工作流瘦身

### sign-and-upload.yml（895 行）

- **生产 job 死重**：:125-139 下载 release zsign 并设 `ZSIGN_BIN`，下一步 :141-165 源码编译补丁版覆盖之——release 二进制是迁移遗留死重；同时每晚全量 `apt-get + g++` 编译无缓存 → 删下载步骤，编译产物按（源码 commit + patch 哈希）缓存；
- **约 250 行重复 YAML → composite actions**（仓库已有 `ssh-debug` 先例）：asc 安装块 ×6（含 pr-checks 与 macOS 变体）、release zsign 安装 ×3、补丁 zsign 源码构建 ×2、fixture 下载构建 ×2、工具 pin env 块 ×6、ssh-debug 调用 + 三布尔守卫 ×6；
- `ZSIGN_SOURCE_COMMIT`/`ZSIGN_SOURCE_SHA256` 两份拷贝无测试强制（`test_workflow_toolchain.py` 只锁 `ASC_SHA256`/`ZSIGN_SHA256` 计数）→ 纳入 toolchain 测试；
- 死条件：`github.event.inputs.debug == true`（:269，`event.inputs.*` 是字符串，恒假；`== 'true'` 分支承载全部功能）及 :406/459/716 的对偶死分支；`DEBUG` env（:82-83）唯一消费者是已无人调用的 `legacy/sync_profiles_asc.py` → 清理；
- 缓存保存条件 :260 第二子句冗余；生产 ssh-debug `if: always()` 且排在 Notify 之前，调试会话会推迟状态通知最多 45 分钟 → 调整顺序或缩短 hold；
- `package-shadow` job 与生产前两阶段逐字重复（约 120 行）——保留作为安全干跑工具可以接受，但应随 composite action 化一并收敛。

### pr-checks.yml

- **补丁 zsign 在 PR CI 从不构建**——补丁 bit-rot 只在 dispatch-only job 或每晚 02:00 UTC 暴露；配合 1.3-#1 的真实 zsign 测试，把源码构建（带缓存）加入 python-tests job；
- `mypy scripts/` 的 `continue-on-error` 转阻塞（理由 `check_changes.py` 已删除）；
- actionlint 的 glob `.github/workflows/*.yml` 不覆盖 `.github/actions/ssh-debug/action.yml` → 补上；
- `pull_request: branches: [main, master]` 中 `main` 不存在 → 删；
- `configs/tasks.toml.example` 无任何解析校验 → 在 `test_config_parser.py` 加一条加载它的测试；
- `workflow-validation` job 重跑全量 pytest 已覆盖的两个测试文件 → 随孤儿引擎删除一并清理。

---

## 六、执行计划

依赖关系：阶段 0 独立先行；阶段 1 先于阶段 2（先删死代码再搬家，避免给死代码搬家）；阶段 3 的瘦身部分依赖阶段 1，保真度部分可与阶段 2 并行；阶段 4 部分依赖阶段 2（qualification 迁移后才能重定向 CI 引用）。

仓库以 openspec 管理变更，目前无活跃 change 认领这份清理（`openspec/changes/` 只剩 archive）——建议为以下阶段立一个 openspec change 作为 owner。

### 阶段 0 — 安全热修（约半天，独立成 PR）

- [ ] 生产 job 门控排除 `qualification_apply`/`qualification_reset_names`（或加前置校验步骤）
- [ ] revalidation 密钥移出 URL（header 或 POST body，同步 web 端点）
- [ ] `publication_enabled` 默认改 `False`；`configs/tasks.toml` 现有任务显式声明；同步 example 与 README
- [ ] `cancellation` 注册 SIGTERM 处理器
- [ ] checkout 全部加 `persist-credentials: false`
- [ ] canary 签名 IPA artifact：停止上传或修订 security.md
- [ ] 每项配一个针对性测试

### 阶段 1 — 纯减法（约 1 天，独立成 PR，行为零变化）

- [ ] 删除孤儿引擎闭环（`pipeline_application.py` + `run_workflow_fixture.py` + 测试 + 2 处 CI 步骤）
- [ ] 删除 `package_commands` 死命令层、`package_runner.run_package_signing`、`inspection.inspect_command` 闭包及对应测试；重挂"publication 禁用先拒"不变量
- [ ] `ports.py` 裁至 4 个活协议；删 `tests/test_ports.py`
- [ ] 删 `legacy/sync_profiles_asc.py`、`legacy/reconcile_icons.py` 及 delegator/测试；抢救 3 个 release-audit 测试
- [ ] 删零消费散件（`StageState`、`reconcile_additive_once`、8 个序列化函数、4 个无消费 delegator、死 fixture、`benchmark_pipeline` 二选一）
- [ ] 删 1.1 凑数测试约 300 行；`test_stage_store.py` 并入 `test_manifest_store.py`
- [ ] 文档漂移 8 处同步修复
- [ ] 预期净删约 1500 行 src + 约 1000 行测试；覆盖率上升

### 阶段 2 — legacy 转正与目录重组（约 2 天）

- [ ] `r2_store`/`app_icon` 迁入 `adapters/publication/`，纳入 mypy strict + 覆盖门槛（预期暴露类型修复量）
- [ ] qualification 五件套整体迁出 legacy（`tools/` 或 CLI 子命令），重定向 CI 引用，退役 `_bootstrap.py` 与 delegator 机制、清空 `legacy/`
- [ ] 顶层重组按 3.1 目标树执行（机械移动；`apple_state_probe` 留 shim 或同步改 workflow）
- [ ] 分层违规 4 处修复（3.2）；`pipeline/environment.py` 提取
- [ ] 重复消除（3.4 全表）；时钟约定统一
- [ ] 神模块拆分（3.3；可拆为独立后续 PR）
- [ ] 异常分类学泄漏收敛（3.5）

### 阶段 3 — 测试保真度（约 1–2 天，可与阶段 2 并行）

- [ ] 真实 zsign 补丁二进制测试接入 PR CI（1.3-#1）
- [ ] 真实 verifier 组装测试，正负两例（1.3-#2）
- [ ] `integration` marker 接入定时 job 或 sign-and-upload（1.3-#3）
- [ ] bundle-graph 发现的真实 Mach-O 用例（1.3-#4）
- [ ] R2 测试迁移 `botocore.Stubber`（1.3-#5）
- [ ] conftest 工厂函数 + 测试横向 import 解耦（1.2）
- [ ] `test_livecontainer_verification_contract` 与 legacy 测试簇收缩（1.2）

### 阶段 4 — CI 瘦身（约 1 天，部分依赖阶段 2）

- [ ] 生产 job 删 release-zsign 下载；补丁构建加缓存
- [ ] 重复 YAML 收敛为 composite actions（第五节清单）
- [ ] 负控制结果接入 comparison job 断言
- [ ] `ZSIGN_SOURCE_*` 纳入 toolchain 测试
- [ ] pr-checks：mypy scripts 转阻塞、actionlint 覆盖 composite action、删 `main` 过滤器、example 配置解析测试
- [ ] 死条件与冗余子句清理；ssh-debug 与 Notify 顺序调整

---

## 附录：审查方法

四个并行审查维度（测试质量、遗留/死代码、架构分层、CI 与健壮性），每维度基于 AST import 图 / grep 引用闭包 / 全文阅读关键模块；高危论断（死协议零引用、孤儿模块消费者、secret 入 URL、`publication_enabled` 默认值、dispatch 门控落穿）经独立二次验证。测试基线为本地全量运行实测。
