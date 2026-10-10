/*
 * 页面里的 DOM 操作（注入到 ChatGPT / Gemini 标签页的隔离环境里，不和网页自己的脚本共享变量）。
 *
 * service worker 用 chrome.scripting.executeScript 注入本文件一次，之后每一步都调
 * globalThis.__aiStudioDom[op](...args)。这里的函数一一对应 backend/engines/browser/driver.py 的
 * PageDriver：找不到元素返回 false / 0 / '' / []，不抛异常。流程本身（等多久、怎么判断出完图）在
 * lib/flow.js 里，由 service worker 驱动——后台标签页的定时器会被浏览器节流，这里不自己轮询。
 *
 * 选择器沿用 Playwright 的写法：逗号分隔多个备选（任一命中就算），支持 :has-text("…")（不分大小写的
 * 包含）和 :text-is("…")（去掉首尾空白后完全相等）两个扩展，只能写在选择器最后一段。
 *
 * 本文件是普通脚本（不是 ES 模块），也能在 node / jsdom 里直接 import 做单测。
 */
(function (root) {
  'use strict';
  const VERSION = 1;
  if (root.__aiStudioDom && root.__aiStudioDom.VERSION === VERSION) return;

  // ---------------------------------------------------------------- 选择器
  /** 按顶层逗号拆开（引号、方括号、圆括号里的逗号不算）。 */
  function splitList(sel) {
    const out = [];
    let depth = 0;
    let quote = '';
    let cur = '';
    for (let i = 0; i < sel.length; i++) {
      const ch = sel[i];
      if (quote) {
        cur += ch;
        if (ch === '\\' && i + 1 < sel.length) {
          cur += sel[++i];
        } else if (ch === quote) {
          quote = '';
        }
        continue;
      }
      if (ch === '"' || ch === "'") quote = ch;
      else if (ch === '(' || ch === '[') depth++;
      else if (ch === ')' || ch === ']') depth = Math.max(0, depth - 1);
      else if (ch === ',' && depth === 0) {
        if (cur.trim()) out.push(cur.trim());
        cur = '';
        continue;
      }
      cur += ch;
    }
    if (cur.trim()) out.push(cur.trim());
    return out;
  }

  const PSEUDO = /:(has-text|text-is)\(\s*(["'])((?:\\.|(?!\2).)*)\2\s*\)/g;

  /** 一个备选拆成标准 CSS（base）和文字过滤条件。 */
  function parsePart(part) {
    const filters = [];
    let base = part.replace(PSEUDO, (_m, kind, _q, text) => {
      filters.push({ kind, text: text.replace(/\\(.)/g, '$1') });
      return '';
    }).trim();
    if (!base || /[\s>+~]$/.test(base)) base += '*';
    return { base, filters };
  }

  function norm(t) {
    return String(t || '').replace(/\s+/g, ' ').trim();
  }

  function textOf(el) {
    const t = typeof el.innerText === 'string' ? el.innerText : el.textContent;
    return norm(t);
  }

  function passes(el, f) {
    const t = textOf(el);
    return f.kind === 'text-is' ? t === norm(f.text) : t.toLowerCase().includes(norm(f.text).toLowerCase());
  }

  /** 选择器命中的所有元素，按文档顺序、去重。写错的备选跳过，不让整条失败。 */
  function queryAll(sel) {
    const seen = new Set();
    const out = [];
    for (const part of splitList(String(sel || ''))) {
      const { base, filters } = parsePart(part);
      let els;
      try {
        els = document.querySelectorAll(base);
      } catch (_e) {
        continue;
      }
      for (const el of els) {
        if (seen.has(el)) continue;
        if (filters.every((f) => passes(el, f))) {
          seen.add(el);
          out.push(el);
        }
      }
    }
    out.sort((a, b) => (a === b ? 0 : a.compareDocumentPosition(b) & 4 ? -1 : 1));
    return out;
  }

  function isVisible(el) {
    if (!el || !el.isConnected) return false;
    const st = root.getComputedStyle ? root.getComputedStyle(el) : null;
    if (st && (st.display === 'none' || st.visibility === 'hidden')) return false;
    if (root.__aiStudioDomNoLayout) {
      // jsdom 没有布局（单测里打开）：只能看祖先有没有 display:none
      for (let p = el.parentElement; p; p = p.parentElement) {
        if (root.getComputedStyle(p).display === 'none') return false;
      }
      return true;
    }
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function firstVisible(sel) {
    const els = queryAll(sel);
    for (let i = 0; i < Math.min(els.length, 20); i++) {
      if (isVisible(els[i])) return els[i];
    }
    return null;
  }

  // ---------------------------------------------------------------- 文件
  function toFiles(files) {
    return (files || []).map((f) => {
      const bin = atob(f.b64);
      const arr = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
      return new File([arr], f.name || 'ref.png', { type: f.mime || 'image/png' });
    });
  }

  function transfer(files) {
    const dt = new DataTransfer();
    for (const f of toFiles(files)) dt.items.add(f);
    return dt;
  }

  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------------------------------------------------------------- 输入
  function fire(el, type, Ctor, init) {
    let ev;
    try {
      ev = new Ctor(type, { bubbles: true, cancelable: true, composed: true, ...init });
    } catch (_e) {
      ev = new Event(type, { bubbles: true, cancelable: true });
    }
    return el.dispatchEvent(ev);
  }

  function valueOf(el) {
    return el.tagName === 'TEXTAREA' || el.tagName === 'INPUT' ? el.value : textOf(el);
  }

  function typedOk(el, text) {
    const got = norm(valueOf(el));
    const want = norm(text);
    return got.includes(want.slice(0, 30).trim()) || got.length >= Math.min(want.length, 30);
  }

  function selectAll(el) {
    if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
      el.select();
      return;
    }
    const sel = root.getSelection && root.getSelection();
    if (!sel) return;
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
  }

  const api = {
    VERSION,
    splitList,
    parsePart,
    queryAll,

    url() {
      return location.href;
    },
    visible(sel) {
      return !!firstVisible(sel);
    },
    count(sel) {
      return queryAll(sel).length;
    },
    enabled(sel) {
      const el = firstVisible(sel);
      return !!el && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
    },
    click(sel) {
      const el = firstVisible(sel);
      if (!el) return false;
      try {
        el.scrollIntoView({ block: 'center' });
      } catch (_e) {
        /* 有的元素不支持 */
      }
      // Radix 之类的下拉菜单在 pointerdown 时打开，普通按钮看 click：整套都发
      const P = root.PointerEvent || root.MouseEvent;
      const opts = { button: 0, buttons: 1, pointerType: 'mouse', isPrimary: true, view: root };
      fire(el, 'pointerdown', P, opts);
      fire(el, 'mousedown', root.MouseEvent, opts);
      fire(el, 'pointerup', P, { ...opts, buttons: 0 });
      fire(el, 'mouseup', root.MouseEvent, { ...opts, buttons: 0 });
      el.click();
      return true;
    },
    /** 输提示词：ProseMirror（ChatGPT）、Quill（Gemini）、普通 textarea 都走 execCommand('insertText')，
     *  它会触发网页自己的输入处理；不行再模拟粘贴纯文本，最后直接写内容 + input 事件。 */
    typeText(sel, text) {
      const el = firstVisible(sel);
      if (!el) return false;
      el.focus();
      selectAll(el);
      try {
        document.execCommand('insertText', false, text);
      } catch (_e) {
        /* 换下一种 */
      }
      if (typedOk(el, text)) return true;
      try {
        const dt = new DataTransfer();
        dt.setData('text/plain', text);
        selectAll(el);
        fire(el, 'paste', root.ClipboardEvent, { clipboardData: dt });
      } catch (_e) {
        /* 换下一种 */
      }
      if (typedOk(el, text)) return true;
      if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') {
        const proto = el.tagName === 'TEXTAREA' ? root.HTMLTextAreaElement : root.HTMLInputElement;
        Object.getOwnPropertyDescriptor(proto.prototype, 'value').set.call(el, text);
      } else {
        el.textContent = text;
      }
      fire(el, 'input', root.InputEvent || Event, { inputType: 'insertText', data: text });
      return typedOk(el, text);
    },
    press(sel, key) {
      const el = firstVisible(sel);
      if (!el) return false;
      el.focus();
      const init = { key, code: key, keyCode: key === 'Enter' ? 13 : 0, which: key === 'Enter' ? 13 : 0 };
      fire(el, 'keydown', root.KeyboardEvent, init);
      fire(el, 'keypress', root.KeyboardEvent, init);
      fire(el, 'keyup', root.KeyboardEvent, init);
      return true;
    },

    /** 把参考图塞进隐藏的 <input type=file>（不弹文件选择框）：DataTransfer 造 FileList，再发 change。
     *  不支持多选的输入框一张一张塞，中间等 1.5 秒。 */
    async setFiles(sel, files) {
      const input = queryAll(sel).find((e) => e.tagName === 'INPUT' && e.type === 'file');
      if (!input || !files || !files.length) return false;
      const batches = files.length === 1 || input.multiple ? [files] : files.map((f) => [f]);
      for (let i = 0; i < batches.length; i++) {
        input.files = transfer(batches[i]).files;
        fire(input, 'input', Event, {});
        fire(input, 'change', Event, {});
        if (i < batches.length - 1) await wait(1500);
      }
      return true;
    },
    /** 往输入框里粘贴图片（ChatGPT、Gemini 都支持粘贴图片）。 */
    pasteFiles(sel, files) {
      const el = firstVisible(sel) || queryAll(sel)[0];
      if (!el || !files || !files.length) return false;
      el.focus();
      fire(el, 'paste', root.ClipboardEvent, { clipboardData: transfer(files) });
      return true;
    },
    /** 把图片拖进输入框。 */
    dropFiles(sel, files) {
      const el = firstVisible(sel) || queryAll(sel)[0];
      if (!el || !files || !files.length) return false;
      const dt = transfer(files);
      for (const type of ['dragenter', 'dragover', 'drop']) fire(el, type, root.DragEvent, { dataTransfer: dt });
      return true;
    },

    /** 最后一个命中元素的文字（网站回复取最后一条）。 */
    text(sel) {
      const els = queryAll(sel);
      if (!els.length) return '';
      const el = els[els.length - 1];
      return String(typeof el.innerText === 'string' ? el.innerText : el.textContent || '');
    },
    images(sel) {
      return queryAll(sel)
        // 不按布局判断显不显示：后台标签页里内容懒渲染，正图也量出 0×0。同一张图的副本在 Flow 里按 alt+尺寸去重
        .filter((e) => e.tagName === 'IMG')
        .map((e) => ({ alt: e.alt || '', src: e.currentSrc || e.src || '', w: e.naturalWidth || 0, h: e.naturalHeight || 0, complete: !!e.complete }));
    },
    /** 在页面里取图（同源请求自动带登录 cookie，blob: 也能取），返回 base64。 */
    async fetchB64(src) {
      try {
        const r = await fetch(src, { credentials: 'include' });
        if (!r.ok) return { error: 'HTTP ' + r.status };
        const blob = await r.blob();
        const url = await new Promise((resolve, reject) => {
          const fr = new FileReader();
          fr.onload = () => resolve(String(fr.result));
          fr.onerror = () => reject(fr.error);
          fr.readAsDataURL(blob);
        });
        return { mime: blob.type || '', b64: url.slice(url.indexOf(',') + 1) };
      } catch (e) {
        return { error: String((e && e.message) || e) };
      }
    },
    /** 看一眼标签页：有没有登录按钮、输入框、是不是正在生成、输入框里有没有用户没发出去的字。 */
    probe(spec) {
      const box = firstVisible(spec.prompt_box);
      return {
        url: location.href,
        loginWall: !!firstVisible(spec.login_wall),
        prompt: !!box,
        busy: !!firstVisible(spec.stop_button),
        draft: box ? norm(valueOf(box)).length > 0 : false,
      };
    },
  };

  root.__aiStudioDom = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
