/*
 * <app-card> —— 可复用的 App 下载卡片 Web Component
 * ---------------------------------------------------------------------------
 * 用法：
 *   <app-card
 *     name="EhPanda"
 *     slug="io.zeroclover.app.ehpanda"
 *     version="2.7.4"
 *     icon="https://itms.zeroclover.io/ehpanda/icon.png"
 *     manifest="https://itms.zeroclover.io/ehpanda/itms.plist">
 *   </app-card>
 *
 * 设计来源：Claude Design「App Download.dc.html」。样式封装在 Shadow DOM 中，
 * 视觉令牌通过 :root 上的 CSS custom properties 注入（透过 shadow 边界继承）。
 * 点击「下载」会以 itms-services 协议触发安装；图标加载失败时回退为首字母色块。
 */
(function () {
  'use strict';

  // 与设计稿一致的占位底色（图标兜底时使用）
  const PALETTE = ['#1a1a18', '#3a3a36', '#56564f'];

  // 由名称派生稳定的色板索引，保证同一 app 兜底色固定
  function paletteFor(key) {
    let h = 0;
    for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
  }

  const template = document.createElement('template');
  template.innerHTML = `
    <style>
      *, *::before, *::after { box-sizing: border-box; }
      :host { display: block; height: 100%; font-family: var(--zc-font-sans, 'Space Grotesk', Helvetica, Arial, sans-serif); }
      :host([hidden]) { display: none; }

      .card {
        background: var(--zc-surface, #fff);
        border: 1px solid var(--zc-border, #e8e8e3);
        border-radius: var(--zc-radius-card, 18px);
        padding: 24px;
        display: flex;
        flex-direction: column;
        align-items: center;
        text-align: center;
        gap: 16px;
        height: 100%;
        transition: border-color .18s ease, transform .18s ease, box-shadow .18s ease;
      }
      .card:hover {
        border-color: var(--zc-ink, #1a1a18);
        transform: translateY(-2px);
        box-shadow: 0 12px 28px rgba(26, 26, 24, 0.08);
      }

      .icon, .icon-fallback {
        width: 76px;
        height: 76px;
        border-radius: var(--zc-radius-icon, 18px);
        background: var(--_bg, #1a1a18);
        flex-shrink: 0;
      }
      .icon { object-fit: cover; display: block; }
      .icon-fallback {
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--zc-bg, #f4f4f2);
        font-family: var(--zc-font-mono, 'Space Mono', monospace);
        font-weight: 700;
        font-size: 30px;
        letter-spacing: -0.02em;
      }

      .meta { display: flex; flex-direction: column; gap: 4px; }
      .name { font-size: 16px; font-weight: 600; letter-spacing: -0.01em; }
      .slug {
        font-family: var(--zc-font-mono, 'Space Mono', monospace);
        font-size: 11px;
        color: var(--zc-muted-2, #a3a39d);
        letter-spacing: 0.04em;
        word-break: break-all;
      }
      .version {
        font-family: var(--zc-font-mono, 'Space Mono', monospace);
        font-size: 10px;
        color: var(--zc-muted, #8a8a85);
        letter-spacing: 0.06em;
      }
      .version[hidden] { display: none; }

      .download {
        margin-top: auto;
        width: 100%;
        padding: 11px 0;
        font-family: inherit;
        font-size: 13px;
        font-weight: 600;
        letter-spacing: 0.02em;
        color: var(--zc-bg, #f4f4f2);
        background: var(--zc-ink, #1a1a18);
        border: none;
        border-radius: var(--zc-radius-btn, 10px);
        cursor: pointer;
        transition: background .18s ease;
      }
      .download:hover { background: #000; }
      .download:focus-visible { outline: 2px solid var(--zc-ink, #1a1a18); outline-offset: 2px; }

      @media (prefers-reduced-motion: reduce) {
        .card, .download { transition: none; }
        .card:hover { transform: none; }
      }
    </style>

    <div class="card" part="card">
      <span class="icon-slot"></span>
      <div class="meta">
        <span class="name"></span>
        <span class="slug"></span>
        <span class="version" hidden></span>
      </div>
      <button class="download" type="button">下载</button>
    </div>
  `;

  class AppCard extends HTMLElement {
    static get observedAttributes() {
      return ['name', 'slug', 'version', 'icon', 'manifest'];
    }

    constructor() {
      super();
      this.attachShadow({ mode: 'open' }).appendChild(template.content.cloneNode(true));
      this.shadowRoot
        .querySelector('.download')
        .addEventListener('click', () => this.download());
    }

    connectedCallback() { this._render(); }
    attributeChangedCallback() { if (this.isConnected) this._render(); }

    get name()     { return this.getAttribute('name') || ''; }
    get slug()     { return this.getAttribute('slug') || ''; }
    get version()  { return this.getAttribute('version') || ''; }
    get icon()     { return this.getAttribute('icon') || ''; }
    get manifest() { return this.getAttribute('manifest') || ''; }

    /** 该卡片是否匹配搜索关键字（按名称或 slug）。 */
    matches(query) {
      const q = (query || '').trim().toLowerCase();
      if (!q) return true;
      return this.name.toLowerCase().includes(q) || this.slug.toLowerCase().includes(q);
    }

    /** 通过 itms-services 协议触发安装。 */
    download() {
      if (!this.manifest) return;
      window.open('itms-services://?action=download-manifest&url=' + this.manifest, '_blank');
    }

    _render() {
      const root = this.shadowRoot;
      this.style.setProperty('--_bg', paletteFor(this.name || this.slug || '?'));

      root.querySelector('.name').textContent = this.name;
      root.querySelector('.slug').textContent = this.slug;

      const ver = root.querySelector('.version');
      ver.textContent = this.version ? 'v' + this.version : '';
      ver.hidden = !this.version;

      const slot = root.querySelector('.icon-slot');
      slot.textContent = '';
      if (this.icon) {
        const img = document.createElement('img');
        img.className = 'icon';
        img.src = this.icon;
        img.alt = this.name + ' 图标';
        img.width = 76;
        img.height = 76;
        img.loading = 'lazy';
        img.decoding = 'async';
        img.addEventListener('error', () => slot.replaceChildren(this._fallback()));
        slot.appendChild(img);
      } else {
        slot.appendChild(this._fallback());
      }

      root.querySelector('.download').setAttribute('aria-label', '下载 ' + this.name);
    }

    _fallback() {
      const el = document.createElement('div');
      el.className = 'icon-fallback';
      el.textContent = (this.name || '?').charAt(0).toUpperCase();
      return el;
    }
  }

  customElements.define('app-card', AppCard);
})();
