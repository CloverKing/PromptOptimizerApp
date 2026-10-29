/*
 * 桌面端注入脚本。
 *
 * 由 Python 在页面加载完成后通过 evaluate_js 注入，不修改上游任何源码。
 * 只做三件事：
 *   1. 打上桌面环境标记，便于将来做差异化处理；
 *   2. 提供轻量 toast（用于「发现新版本」「端口被占用」等系统级提示）；
 *   3. 拦住把文件拖到窗口空白处导致页面被浏览器直接打开的行为。
 */
(function () {
  'use strict';

  if (window.__poDesktop && window.__poDesktop.__installed) {
    return;
  }

  var INFO = __PO_INFO__;

  var STYLE_ID = 'po-desktop-style';
  var HOST_ID = 'po-desktop-toasts';

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = [
      '#' + HOST_ID + '{position:fixed;right:16px;bottom:16px;z-index:2147483647;display:flex;flex-direction:column;gap:8px;max-width:min(420px,calc(100vw - 32px));}',
      '.po-desktop-toast{display:flex;align-items:center;gap:10px;padding:10px 14px;border-radius:8px;',
      'background:#ffffff;color:#303133;border:1px solid #e4e7ed;box-shadow:0 6px 24px rgba(0,0,0,.12);',
      'font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;',
      'animation:po-desktop-in .18s ease-out;}',
      '.po-desktop-toast span{flex:1;min-width:0;word-break:break-word;}',
      '.po-desktop-toast button{flex:none;border:none;background:#409eff;color:#fff;border-radius:6px;',
      'padding:5px 10px;font-size:13px;cursor:pointer;}',
      '.po-desktop-toast button:hover{background:#337ecc;}',
      '.po-desktop-toast .po-close{background:transparent;color:#909399;padding:0 2px;font-size:16px;line-height:1;}',
      '.po-desktop-toast .po-close:hover{background:transparent;color:#606266;}',
      '@keyframes po-desktop-in{from{opacity:0;transform:translateY(8px);}to{opacity:1;transform:none;}}',
      '@media (prefers-color-scheme:dark){.po-desktop-toast{background:#1f1f1f;color:#e5eaf3;border-color:#3c3c3c;',
      'box-shadow:0 6px 24px rgba(0,0,0,.5);}.po-desktop-toast .po-close{color:#8d9095;}}'
    ].join('');
    (document.head || document.documentElement).appendChild(style);
  }

  function toast(message, options) {
    if (!message) return function () {};
    ensureStyle();
    var host = document.getElementById(HOST_ID);
    if (!host) {
      host = document.createElement('div');
      host.id = HOST_ID;
      (document.body || document.documentElement).appendChild(host);
    }

    var el = document.createElement('div');
    el.className = 'po-desktop-toast';

    var text = document.createElement('span');
    text.textContent = String(message);
    el.appendChild(text);

    var dismissed = false;
    function dismiss() {
      if (dismissed) return;
      dismissed = true;
      if (el.parentNode) el.parentNode.removeChild(el);
    }

    var opts = options || {};
    if (opts.actionText) {
      var action = document.createElement('button');
      action.textContent = opts.actionText;
      action.addEventListener('click', function () {
        openExternal(opts.actionUrl);
        dismiss();
      });
      el.appendChild(action);
    }

    var close = document.createElement('button');
    close.className = 'po-close';
    close.textContent = '\u00d7';
    close.setAttribute('aria-label', 'close');
    close.addEventListener('click', dismiss);
    el.appendChild(close);

    host.appendChild(el);
    var duration = typeof opts.duration === 'number' ? opts.duration : 15000;
    if (duration > 0) setTimeout(dismiss, duration);
    return dismiss;
  }

  function api() {
    return (window.pywebview && window.pywebview.api) || null;
  }

  function openExternal(url) {
    if (!url) return;
    var bridge = api();
    if (bridge && typeof bridge.open_external === 'function') {
      bridge.open_external(url);
    } else {
      window.open(url, '_blank');
    }
  }

  // 阻止「拖文件到空白处」触发页面跳转；应用自身的拖放逻辑不受影响。
  ['dragover', 'drop'].forEach(function (type) {
    window.addEventListener(type, function (event) {
      event.preventDefault();
    }, false);
  });

  window.__poDesktop = {
    __installed: true,
    isDesktop: true,
    info: INFO,
    openExternal: openExternal,
    toast: toast
  };

  if (document.documentElement) {
    document.documentElement.classList.add('po-desktop');
  }
})();
