// 假的标签页（扩展 flow.js 的 driver）：照搬 backend/tests/fakes/fake_browser.py。
// 默认已登录、有输入框；afterSubmit([[秒, fn], ...]) 安排提交后第几秒发生什么；sleep() 推进假时钟。

const PNG_HEAD = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

export class FakePage {
  constructor(spec, { loggedIn = true, hasFileInput = true, promptBox = true, pasteWorks = false } = {}) {
    this.spec = spec;
    this.t = 0;
    this.calls = [];
    this.cookies = loggedIn ? [spec.session_cookies[0]] : [];
    this.shown = new Set([spec.send_button]);
    if (promptBox) this.shown.add(spec.prompt_box);
    if (!loggedIn) this.shown.add(spec.login_wall);
    this.hasFileInput = hasFileInput;
    this.pasteWorks = pasteWorks;
    this.attachments = 0;
    this.reply = '';
    this.alert = '';
    this.results = [];
    this.mine = [];
    this.blobs = new Map();
    this.script = [];
    this.submittedAt = null;
    this.urlNow = 'about:blank';
  }

  afterSubmit(steps) {
    this.script = [...steps].sort((a, b) => a[0] - b[0]);
    return this;
  }
  show(sel) { this.shown.add(sel); }
  hide(sel) { this.shown.delete(sel); }
  addResult(src, w = 1024, h = 1536, b64 = PNG_HEAD) {
    this.results.push({ src, w, h, complete: true });
    this.blobs.set(src, b64);
  }
  finish(src, w = 1024, h = 1536) {
    this.hide(this.spec.stop_button);
    this.results = [];
    this.addResult(src, w, h);
  }
  tick() {
    if (this.submittedAt === null) return;
    while (this.script.length && this.submittedAt + this.script[0][0] <= this.t) {
      const [, fn] = this.script.shift();
      fn(this);
    }
  }
  clock = () => this.t;

  // ------------------------------------------------------------ driver
  async goto(url) { this.calls.push(['goto', url]); this.urlNow = url; }
  async url() { return this.urlNow; }
  async visible(sel) { return this.shown.has(sel); }
  async count(sel) {
    if (sel === this.spec.attachment) return this.attachments;
    return this.shown.has(sel) ? 1 : 0;
  }
  async enabled(sel) { return this.shown.has(sel); }
  async click(sel) {
    this.calls.push(['click', sel]);
    if (sel === this.spec.send_button && this.shown.has(sel)) {
      this.submittedAt = this.t;
      return true;
    }
    if (sel === this.spec.stop_button && this.shown.has(sel)) {
      this.hide(sel);
      return true;
    }
    return this.shown.has(sel);
  }
  async typeText(sel, text) {
    if (!this.shown.has(sel)) return false;
    this.calls.push(['type', text]);
    return true;
  }
  async press(_sel, key) { this.calls.push(['press', key]); return false; }
  async setFiles(_sel, files) {
    if (!this.hasFileInput) return false;
    this.calls.push(['setFiles', files.map((f) => f.name)]);
    this.attachments += files.length;
    this.mine.push(...files.map((_f, i) => ({ src: `blob:ref${i}`, w: 512, h: 512, complete: true })));
    return true;
  }
  async pasteFiles(_sel, files) {
    this.calls.push(['pasteFiles', files.map((f) => f.name)]);
    if (this.pasteWorks) this.attachments += files.length;
    return true;
  }
  async dropFiles(_sel, files) {
    this.calls.push(['dropFiles', files.map((f) => f.name)]);
    return false;
  }
  async text(sel) {
    if (sel === this.spec.assistant_turn) return this.reply;
    if (sel === this.spec.alert) return this.alert;
    return '';
  }
  async images(sel) {
    if (sel === this.spec.result_images) return [...this.results];
    if (sel === this.spec.user_images) return [...this.mine];
    return [];
  }
  async fetchImage(src) {
    this.calls.push(['fetch', src]);
    if (!this.blobs.has(src)) throw new Error('HTTP 403');
    return { mime: 'image/png', b64: this.blobs.get(src), via: 'page' };
  }
  async cookieNames() { return [...this.cookies]; }
  async sleep(s) {
    this.t += s;
    this.tick();
  }
}

export const PNG_B64 = PNG_HEAD;
