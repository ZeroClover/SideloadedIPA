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
  { name: 'EhPanda',   dir: 'ehpanda',   bundleId: 'io.zeroclover.app.ehpanda', version: '2.7.4'  },
  { name: 'FEhViewer', dir: 'fehviewer', bundleId: 'cn.honjow.fehv',            version: '1.9.2'  },
  { name: 'Sonolus',   dir: 'Sonolus',   bundleId: 'io.zeroclover.app.sonolus', version: '0.7.5'  },
  { name: 'JHenTai',   dir: 'JHenTai',   bundleId: 'top.jtmonster.jhentai',     version: '8.0.14' },
  { name: 'Asspp',     dir: 'Asspp',     bundleId: 'wiki.qaq.Asspp',            version: '4.2.0'  },
  { name: 'PiliPlus',  dir: 'PiliPlus',  bundleId: 'com.example.piliplus',      version: '2.0.9'  },
  { name: 'StikDebug', dir: 'StikDebug', bundleId: 'com.stik.stikdebug',        version: '3.1.6'  },
];
