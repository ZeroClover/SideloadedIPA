# 项目审查与优化记录（2026-10-04）

## 结论与范围

现有的 domain / ports / adapters / stages 分层、不可变领域模型、内容寻址缓存、独立签名验证、发布回滚与延迟清理机制值得保留。进一步优化应聚焦边界正确性、工具链更新和生产路径的验证，而不是增加框架或再次重写签名流程。

本次检查了依赖与 CI 声明，以及下载、重试、编排、发布、打包和 Web 分发的关键路径。没有更改任务配置、Apple 资源同步策略、签名补丁、缓存摘要规则、entitlement 策略、CLI 命令或 OTA plist 格式；没有调用生产 Apple/R2/Vercel 服务。

## 已实施的改进

| 问题 | 改动 | 回归保护 |
|---|---|---|
| HTTP 声明长度与实际传输长度未核对，直接 URL 缺少外部摘要时可能接收不完整数据 | 提交临时文件前核对 Content-Length；保持既有大小错误码并清理临时文件 | 覆盖无外部大小证据、有外部大小证据两种截断情况 |
| 下载文件先发布再修改权限，权限修改失败时可能留下已提交文件 | 在临时文件上设置只读权限，再原子替换 | 原有只读文件、摘要及清理测试 |
| HTTPError 也拥有响应体，但错误/重试路径未显式关闭 | GitHub API 和下载器都关闭错误响应体 | 覆盖 404、503、GitHub 403 |
| 下载和重试策略接受非整数计数、布尔值、NaN 或无穷值 | 在策略构造时验证计数与有限时间参数 | 参数化边界测试 |
| jitter 在基础延迟封顶后仍可能突破 max_delay_seconds | 对最终等待时间再次封顶 | 最大 jitter 的回归测试 |
| 发布先上传 IPA，再发现旧注册表结构异常 | 提取共享的注册表结构校验，在上传前执行 | 格式异常时只允许读取，不允许上传/发布 |
| Web 刷新密钥使用普通字符串比较，401 缺少禁止缓存响应头 | 使用 SHA-256 定长摘要与 timingSafeEqual；401 返回 no-store | 相同长度、不同长度和不同 UTF-8 字节长度的错误密钥测试 |
| 注册表解码先冻结临时数组再 slice，冻结被丢弃且多一次复制 | 保留每个条目的冻结，直接返回映射数组 | 既有解码及交付测试，返回类型和数组可变性不变 |
| Python sdist 未明确隔离前端目录，构建会扫描 node_modules | 显式只打包源码、pyproject、README、LICENSE；CI 增加 uv build | 打包边界测试；实际构建 sdist，再从 sdist 构建 wheel |

先增加的下载/重试/HTTP 回归测试在修复前出现 **20 个失败**，修复后通过，确认测试确实覆盖了原有缺陷。

## 稳定版本核查与升级

版本取自当日 PyPI JSON、npm 注册表、GitHub 官方仓库 release/tag API。两份锁文件重新生成，Python 未发现落后的已安装包；npm 唯一仍标记 outdated 的直接依赖是刻意保留在 Node 22 系列的 @types/node。

| 依赖/工具 | 原版本 | 本次版本/决定 |
|---|---|---|
| boto3 / botocore | 1.43.51 | 1.43.108 |
| cryptography | 49.0.0 | 50.0.2 |
| urllib3（传递依赖） | 2.7.0 | 2.8.0 |
| pytest-cov | 7.0.0 | 7.1.0 |
| mypy | 1.19.1 | 2.4.0，strict 检查通过 |
| isort | 7.0.0 | 9.0.2 |
| zizmor | 1.28.0 | 1.30.1 |
| Hatchling | 1.31.0 | 1.32.4 |
| uv | 0.11.31 | 0.12.23，项目与 CI 同步精确版本 |
| Python | 3.11.15 | 3.11.17，保留受支持的 3.11 系列 |
| Node.js | 22.23.1 | 22.23.3，保留受支持的 22 系列 |
| React / react-dom | 19.2.7 | 19.3.0 |
| TypeScript | 5.9.3 | 7.0.2，Next 构建及独立类型检查通过 |
| tsx | 4.23.1 | 4.23.15 |
| @types/react / @types/react-dom | 19.2.17 / 19.2.3 | 19.3.0 |
| @types/node | 22.20.1 | 22.20.5，与运行时主版本一致，不升级到 26.6.4 |
| setup-uv Action | 9.0.0 | 10.2.0，仍固定 commit SHA；核对了所使用的输入契约 |
| LIEF / Pillow / pyasn1 / pyasn1-modules | 1.0.0 / 12.3.0 / 0.6.4 / 0.4.2 | 已是当前稳定版；保持锁定结果 |
| pytest / Black | 9.1.1 / 26.5.1 | 已是当前稳定版 |
| Next.js / Vercel Analytics / Speed Insights | 16.3.8 / 2.0.1 / 2.0.0 | 已是当前稳定版 |
| checkout / setup-node / cache / actionlint | 7.0.1 / 7.0.0 / 6.1.0 / 1.7.12 | 已是当前稳定版；固定身份不变 |
| ASC CLI | 3.1.1 | 最新为 5.9.2；暂不替换现有命令契约与 fixtures |
| zsign | 带项目补丁的 1.1.1 | 最新为 1.1.2；暂不替换已资格认定的源码、校验和及补丁 |

原 Python 审计有 8 条漏洞记录，包含不同数据源对同一问题的重复记录；涉及 cryptography 的 PKCS#7 解密问题及 urllib3 的代理 TLS、deflate 循环和 chunk-size 内存问题。升级后 uv audit 无已知漏洞。Web 审计也无已知漏洞，未增加漏洞豁免。

## 验证证据

- 基线：Python **887 passed / 3 skipped**，覆盖率 **95.19%**；Web **37 passed**，plist golden 字节一致。
- 更新后：Python 3.11.17 下 **914 passed / 3 skipped**，覆盖率 **95.23%**，95% 门槛保持不变。
- Node 22.23.3 下：Web **40 passed**；独立 TypeScript 检查、fixture 模式 Next.js 生产构建通过；plist golden 仍逐字节一致。
- strict mypy、Black、isort 通过；变动的 Python 测试文件也单独检查格式。
- actionlint 1.7.12 校验通过；其下载产物先核对官方 SHA-256。zizmor 1.30.1 无 high 级发现，使用与项目 CI 相同的离线检查模式。
- uv audit 与项目 npm 审计门通过；uv lock --check 通过。
- sdist 与 wheel 实际构建成功；检查分发包没有前端、本地工作目录、环境文件或 pycache。

**限制：**3 项跳过测试分别依赖真实 patched zsign 和两份可选下载的 LiveContainer IPA。本次未执行真实开发者账号签名、物理设备安装或线上发布，因此不能把离线回归通过等同于这些环境的完整验收。GitHub Actions 本身尚需远端 CI 验证。

## 后续优化建议（未冒险改动）

1. **ASC/zsign 独立升级。**用新 CLI 重建 profile/bundle/capability 命令契约，验证新 zsign 上的补丁适用性、逐 bundle 权限与 XML/DER 一致性，再做真实签名与设备安装验收。不能仅修改版本号或 checksum。
2. **精简编排器的测试专用转发。**ProductionPipeline 仍有 _store、_cache、_record_success 等薄转发，部分只供测试使用。下一轮应让测试依赖对应 stage/store 的实际接口，随后移除无生产用途的包装；不要添加新的兼容层。
3. **子进程的真正资源上限。**SubprocessRunner 当前通过 PIPE 收集完整输出，再裁剪返回证据；max_output_bytes 不限制捕获阶段内存。将输出捕获改为有界流/受控 spool 时，必须同时保护跨块密钥脱敏、头尾证据、超时与错误码契约，宜作为独立改动验证。
4. **外部并发发布。**当前 workflow 的串行化适合现有单发布者。如果未来允许其他写入者同时更新 R2 注册表，应增加基于对象版本/ETag 的条件写入；否则本地快照回滚可能覆盖其他写入者。不要在尚无此需求时增加分布式锁或自制事务框架。

这些事项与本次低风险修复分开处理，避免为了“精简”削弱独立验证、发布回滚、缓存身份或实体设备验收。
