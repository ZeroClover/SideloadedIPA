/*
 * App 数据源（单一事实来源）
 * ---------------------------------------------------------------------------
 * 字段说明：
 *   name      显示名称
 *   dir       在 itms.zeroclover.io 下的目录名（用于拼接 icon / itms.plist）
 *   bundleId  iOS Bundle Identifier，作为卡片副标题（slug）
 *   version   当前已签名的版本号
 *
 * icon / manifest 链接由 app.js 依据 dir 统一拼接，新增 app 只需在此追加一条。
 * 数据应与 itms.zeroclover.io 实际已部署的目录保持一致。
 */
window.ZC_APPS = [
  { name: 'EhPanda',   dir: 'ehpanda',   bundleId: 'io.zeroclover.app.ehpanda',   version: '2.7.4'  },
  { name: 'FEhViewer', dir: 'fehviewer', bundleId: 'io.zeroclover.app.fehviewer', version: '1.5.4'  },
  { name: 'Sonolus',   dir: 'Sonolus',   bundleId: 'io.zeroclover.app.sonolus',   version: '0.7.5'  },
  { name: 'JHenTai',   dir: 'JHenTai',   bundleId: 'io.zeroclover.app.jhentai',   version: '7.4.10' },
  { name: 'Asspp',     dir: 'Asspp',     bundleId: 'io.zeroclover.app.asspp',     version: '3.0.24' },
  { name: 'PiliPlus',  dir: 'PiliPlus',  bundleId: 'io.zeroclover.app.piliplus',  version: '1.0.0'  },
  { name: 'StikDebug', dir: 'StikDebug', bundleId: 'io.zeroclover.app.stikdebug', version: '3.1.6'  },
];
