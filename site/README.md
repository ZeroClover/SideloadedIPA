# ITMS 下载页面（`site/`）

`itms.zeroclover.io` 的下载中心页面源码。展示所有已重签名的 app，访客点击「下载」即通过
`itms-services://` 协议安装。一个零构建、零运行时依赖的静态站点 —— 直接部署到 Plesk 网站根目录即可。

## 设计来源

视觉来自 Claude Design 项目 **App Download.dc.html**
（`claude.ai/design/p/7354fddf-edb9-4ecc-ab7e-f382bcb92589`）。设计稿基于 Claude Design 的私有
运行时（`x-dc` / dc-runtime），本目录用**原生 Web 标准重新实现**，不携带该运行时：

- 暖白 `#f4f4f2` 背景、近黑 `#1a1a18` 文字、Space Grotesk + Space Mono 字体；
- `auto-fill minmax(220px, 1fr)` 自适应卡片网格；
- 实时搜索过滤 + 空状态。

与设计稿的有意差异：设计稿用首字母色块占位，本实现改用服务器上的**真实图标**，并保留首字母
色块作为加载失败兜底。

## 文件结构

| 文件 | 职责 |
| --- | --- |
| [`index.html`](index.html) | 页面骨架：头部、搜索框、网格容器、空状态 |
| [`styles.css`](styles.css) | 页面级样式 + `:root` 设计令牌（CSS custom properties） |
| [`apps.js`](apps.js) | **数据源**：app 列表（单一事实来源） |
| [`app-card.js`](app-card.js) | **可复用组件** `<app-card>`：封装在 Shadow DOM 中的下载卡片 |
| [`app.js`](app.js) | 装配：渲染卡片 + 绑定搜索过滤 |

设计令牌定义在 `styles.css` 的 `:root`，透过 Shadow DOM 边界继承，`<app-card>` 内部直接复用，
保证页面与组件视觉一致。

## 新增 / 更新一个 app

**多数情况下无需手动改动。** 签名流水线（[`../configs/tasks.toml`](../configs/tasks.toml)）
每次重签后会自动维护 [`apps.js`](apps.js)：按 app 实际部署目录（`dir`）匹配，刷新已有 app 的
`version` / `bundleId`，并为新 app 追加一条记录；同时递增 [`index.html`](index.html) 中
`apps.js?v=` 缓存版本号，并把改动 commit 回仓库（详见
[`../scripts/site_update.py`](../scripts/site_update.py)）。流水线**保留**已有条目的显示
`name`（视为人工策展），只刷新版本与 bundleId。

手动维护仅用于**不经流水线**的 app（无对应 `[[tasks]]`，如 EhPanda / Sonolus）—— 在
`ZC_APPS` 数组追加或修改一条记录即可：

```js
{ name: 'Halo', dir: 'Halo', bundleId: 'io.zeroclover.app.halo', version: '1.0.0' },
```

- `dir` 必须与 `itms.zeroclover.io` 下的实际目录名一致（大小写敏感），也是流水线匹配的键；
- `icon`（`/{dir}/icon.png`）与下载清单（`/{dir}/itms.plist`）链接由 `app.js` 自动按 `dir` 拼接；
- `bundleId` 显示为卡片副标题，`version` 显示为版本标签（可省略）。

数据应与服务器实际部署的目录保持一致 —— app 目录、`itms.plist` 与下载页面均由签名流水线
生成并上传。

## 本地预览

需通过 HTTP 提供（图标与 itms.plist 走 `https://itms.zeroclover.io` 绝对地址）：

```bash
cd site && python3 -m http.server 8080
# 打开 http://localhost:8080
```

> `itms-services://` 安装仅在 iOS Safari 中生效，桌面浏览器点击「下载」无反应属正常。

## 部署

把 `site/` 内的文件复制到网站根目录
`/var/www/vhosts/zeroclover.io/itms.zeroclover.io/`（与各 app 目录平级，`index.html` 覆盖原页面）。

**Cloudflare 缓存要点**：HTML 响应是 `DYNAMIC`（不被边缘缓存），但**同名的 JS/CSS 会被 CF 按文件名缓存**。
因此每次改动 JS/CSS 后，必须递增 `index.html` 里资源引用的 `?v=N` 版本号（如 `app-card.js?v=3`），
否则用户会继续拿到 CF 边缘缓存的旧文件（表现为"修复已部署却仍是旧效果"）。手头无 CF API token 可主动 purge。
