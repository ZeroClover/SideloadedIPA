/*
 * 页面装配
 * ---------------------------------------------------------------------------
 * 1. 读取 window.ZC_APPS，为每个 app 生成一个 <app-card>；
 * 2. icon / manifest 链接按 dir 统一拼接到 ASSET_BASE；
 * 3. 绑定搜索框，按名称 / slug 实时过滤，并维护空状态与计数。
 */
(function () {
  'use strict';

  const ASSET_BASE = 'https://itms.zeroclover.io';

  const apps    = window.ZC_APPS || [];
  const grid    = document.getElementById('grid');
  const search  = document.getElementById('search');
  const empty   = document.getElementById('empty');
  const emptyQ  = document.getElementById('empty-query');
  const count   = document.getElementById('count');

  // ── 渲染卡片 ──────────────────────────────────────────────
  const cards = apps.map(function (app) {
    const card = document.createElement('app-card');
    card.setAttribute('name', app.name);
    card.setAttribute('slug', app.bundleId);
    if (app.version) card.setAttribute('version', app.version);
    card.setAttribute('icon', ASSET_BASE + '/' + app.dir + '/icon.png');
    card.setAttribute('manifest', ASSET_BASE + '/' + app.dir + '/itms.plist');
    grid.appendChild(card);
    return card;
  });

  count.textContent = apps.length + ' 个应用';

  // ── 搜索过滤 ──────────────────────────────────────────────
  function applyFilter(query) {
    let visible = 0;
    cards.forEach(function (card) {
      const ok = card.matches(query);
      card.hidden = !ok;
      if (ok) visible++;
    });
    empty.hidden = visible !== 0;
    emptyQ.textContent = query;
  }

  search.addEventListener('input', function (e) {
    applyFilter(e.target.value);
  });
})();
