// 扩展的生成器页流程（browser-extension/lib/flow.js 的 runGenerator，即梦 / 可灵）：假 driver 跑通成功、
// 没登录、积分不足、审核不通过、超时、选项找不到、尾帧，以及 content/dom.js 里生成器页用的几个操作。
import { beforeAll, beforeEach, describe, expect, it } from 'vitest';
import '../../../browser-extension/content/dom.js';
import { CARD_APPEAR, Canceled, FlowError, pickOption, runGenerator, showsValue } from '../../../browser-extension/lib/flow.js';
import { FEATURES } from '../../../browser-extension/lib/protocol.js';
import {
  GENERATOR_SITES, JIMENG_CN, JIMENG_GLOBAL, KLING_CN, KLING_GLOBAL, SITES, VIDEO_SITES,
} from '../../../browser-extension/sites/index.js';
import { MP4_B64, PNG_B64 } from './fakePage.js';

const FIRST = { name: 'first.png', mime: 'image/png', b64: PNG_B64 };
const END = { name: 'end.png', mime: 'image/png', b64: PNG_B64 };
const VID = 'https://v9-artist.vlabvod.com/abc/video.mp4';

const opt = (head, extra = {}) => ({ text: `${head} ${extra.desc || ''}`.trim(), head, disabled: !!extra.disabled, selected: !!extra.selected });

/** 即梦生成页上各个下拉 / 弹层的选项（照 2026-10-12 实测的文字）。 */
const JIMENG_MENUS = {
  创作类型: ['Agent 模式', '图片生成', '视频生成', '音乐生成', '音频生成', '数字人', '动作模仿'].map((h) => opt(h)),
  模型: [
    opt('即梦 Seedance 2.5', { desc: '样片模式' }), opt('Seedance 2.5', { desc: '最强模型，支持 50 个参考' }),
    opt('Seedance 2.0 mini', { desc: '性价比' }), opt('Seedance 2.0 Fast VIP'), opt('Seedance 2.0 VIP'),
    opt('Seedance 2.0 Fast'), opt('Seedance 2.0', { desc: '暂不支持真人人脸' }),
    opt('Seedance 1.0', { disabled: true }), opt('HappyHorse 1.1'), opt('Wan 3.0'),
  ],
  参考方式: ['全能参考', '首尾帧', '智能多帧', '智能编辑', '超长视频'].map((h) => opt(h)),
  画幅: ['21:9', '16:9', '4:3', '1:1', '3:4', '9:16', '720P', '1', '2', '3', '4'].map((h) => opt(h)),
};

/**
 * 假的生成器页（runGenerator 的 driver）。triggers：每个「打开」选择器上的按钮文字；点开某个按钮后
 * options(那一项的 option 选择器) 返回 menus[那一项的 name]，clickAt 选中后按钮文字变成选中的值、弹层关上。
 * afterSubmit([[秒, fn]]) 安排点了生成后第几秒发生什么；sleep() 推进假时钟。
 */
class FakeGen {
  constructor(spec, { loggedIn = true, menus = JIMENG_MENUS, triggers = null, credits = '120', cost = '30',
    fileInputWorks = true, durationInput = true } = {}) {
    this.spec = spec;
    this.t = 0;
    this.calls = [];
    this.cookies = loggedIn ? [spec.session_cookies[0]] : [];
    this.loggedIn = loggedIn;
    this.menus = menus;
    this.triggers = triggers || {
      [spec.options[0].open]: ['Agent 模式', 'Seedance 2.0 mini', '全能参考'],
      'button.lv-btn': ['16:9 720P 1', '5s'],
    };
    this.credits = credits;
    this.cost = cost;
    this.fileInputWorks = fileInputWorks;
    this.durationInput = durationInput;
    this.openEntry = null; // 现在开着的是哪一项（{entry, sel, idx}）
    this.thumbs = 0;
    this.files = [];
    this.prompt = '';
    this.alert = '';
    this.cards_ = [{ key: '', text: '之前的一条 已完成', video: 'https://old/video.mp4', failed: false }];
    this.blobs = new Map([[VID, MP4_B64]]);
    this.script = [];
    this.submittedAt = null;
    this.urlNow = 'about:blank';
  }

  afterSubmit(steps) {
    this.script = [...steps].sort((a, b) => a[0] - b[0]);
    return this;
  }
  tick() {
    if (this.submittedAt == null) return;
    while (this.script.length && this.t - this.submittedAt >= this.script[0][0]) this.script.shift()[1](this);
  }
  /** 新卡片：即梦新任务在最后（result_new_at = last），可灵在最前。 */
  addCard(card) {
    const c = { key: '', text: '', video: '', failed: false, ...card };
    if (this.spec.result_new_at === 'last') this.cards_.push(c);
    else this.cards_.unshift(c);
    return c;
  }
  entries() {
    return [...this.spec.options, { ...this.spec.duration, name: '时长' }];
  }
  entryFor(sel, text) {
    return this.entries().find((o) => o.open === sel && (!o.openMatch || new RegExp(o.openMatch, 'i').test(text)));
  }

  async goto(url) { this.calls.push(['goto', url]); this.urlNow = url; }
  async url() { return this.urlNow; }
  async visible(sel) {
    if (sel === this.spec.prompt_box) return true;
    if (sel === this.spec.login_wall) return !this.loggedIn;
    if (this.openEntry && sel === this.openEntry.entry.panel) return true;
    return false;
  }
  async count(sel) { return sel === this.spec.upload.thumb ? this.thumbs : 0; }
  async composerImages() { return 0; }
  async enabled(sel) { return sel === this.spec.send_button; }
  async click(sel) {
    this.calls.push(['click', sel]);
    if (sel !== this.spec.send_button) return false;
    this.submittedAt = this.t;
    this.tick();
    return true;
  }
  /** 这个选项选择器现在对应哪一项：直接摆在页面上的（没有 open）；或者开着的弹层里的（几项共用一个选项选择器时
   *  按开着的那个按钮认）。 */
  menuEntry(sel) {
    const cands = this.entries().filter((o) => o.option === sel);
    const flat = cands.find((o) => !o.open);
    if (flat) return flat;
    if (!this.openEntry) return null;
    if (cands.length === 1) return cands[0];
    const { sel: tsel, idx } = this.openEntry;
    return cands.find((o) => o === this.entryFor(tsel, this.triggers[tsel][idx])) || null;
  }
  async options(sel) {
    if (this.triggers[sel]) return this.triggers[sel].map((t) => ({ text: t, head: t, disabled: false, selected: false }));
    const e = this.menuEntry(sel);
    return e ? this.menus[e.name] || [] : [];
  }
  async clickAt(sel, i) {
    this.calls.push(['clickAt', sel, i]);
    if (this.triggers[sel]) {
      const text = this.triggers[sel][i];
      const entry = this.entryFor(sel, text);
      this.openEntry = this.openEntry ? null : { entry, sel, idx: i };
      return true;
    }
    const e = this.menuEntry(sel);
    const item = (this.menus[e.name] || [])[i];
    this.calls.push(['pick', e.name, item.head]);
    if (this.openEntry) {
      const { sel: tsel, idx } = this.openEntry;
      this.triggers[tsel][idx] = e.name === '时长' ? item.head : e.name === '画幅' ? `${item.head} 720P 1` : item.head;
      this.openEntry = null;
    }
    return true;
  }
  async press(_sel, key) { if (key === 'Escape') this.openEntry = null; return true; }
  async fillInput(sel, value) {
    if (!this.durationInput || sel !== this.spec.duration.input || !this.openEntry) return false;
    this.calls.push(['fill', value]);
    this.triggers[this.openEntry.sel][this.openEntry.idx] = `${value}s`;
    return true;
  }
  async setFilesAt(sel, i, files) {
    this.calls.push(['setFilesAt', i, files.map((f) => f.name)]);
    this.files.push(...files.map((f) => f.name));
    if (this.fileInputWorks) this.thumbs += 1;
    return true;
  }
  async pasteFiles() { return false; }
  async dropFiles() { return false; }
  async fileInputs() { return { total: 1, near: 1 }; }
  async typeText(sel, text) {
    if (sel !== this.spec.prompt_box) return false;
    this.calls.push(['type', text]);
    this.prompt = text;
    return true;
  }
  async text(sel) {
    if (sel === this.spec.credits) return this.credits;
    if (sel === this.spec.cost) return this.cost;
    if (sel === this.spec.alert) return this.alert;
    return '';
  }
  async cards(sel) { return sel === this.spec.result_card ? this.cards_.map((c) => ({ ...c })) : []; }
  async fetchImage(src) {
    this.calls.push(['fetch', src]);
    if (!this.blobs.has(src)) throw new Error('HTTP 403');
    return { mime: 'video/mp4', b64: this.blobs.get(src), via: 'page' };
  }
  async cookieNames() { return [...this.cookies]; }
  async sleep(s) {
    this.t += s;
    this.tick();
  }
}

async function run(page, { spec = JIMENG_CN, refs = [FIRST], timeout = 900, canceled = () => false, generator = {}, ...job } = {}) {
  const logs = [];
  const prog = [];
  const out = await runGenerator(spec, { prompt: '女孩 回头\n微笑', refs, timeout, aspect: '9:16', generator, ...job }, page, {
    isCanceled: canceled, log: (m) => logs.push(m), progress: (v, m) => prog.push([v, m]), clock: () => page.t, poll: 1,
  });
  return { out, logs, prog };
}

async function fails(promise) {
  try {
    await promise;
  } catch (e) {
    return e;
  }
  throw new Error('没有失败');
}

const done = (at = 60) => [
  [3, (p) => p.addCard({ text: '生成中 排队中…' })],
  [at, (p) => { p.cards_.find((c) => c.text.startsWith('生成中')).video = VID; }],
];

describe('pickOption / showsValue', () => {
  it('先找完全相同的；按开头匹配时后面紧跟字母数字的不算（不会把 Seedance 2.0 选成 2.0 mini）', () => {
    const items = JIMENG_MENUS.模型;
    expect(items[pickOption(items, 'Seedance 2.0')].head).toBe('Seedance 2.0');
    expect(items[pickOption(items, 'seedance 2.0 MINI')].head).toBe('Seedance 2.0 mini');
    expect(pickOption([opt('Seedance 2.0 mini', { desc: '性价比' })], 'Seedance 2.0')).toBe(-1);
    expect(pickOption([{ text: 'Seedance 2.0 暂不支持真人人脸' }], 'Seedance 2.0')).toBe(0);
    expect(pickOption(items, 'Seedance 9')).toBe(-1);
    expect(pickOption([opt('9:16'), opt('16:9')], '16:9', 'exact')).toBe(1);
    expect(pickOption([opt('Video generation')], ['视频生成', 'Video generation'])).toBe(0);
    expect(pickOption([{ text: '生成方式 图生视频 文生视频' }], '图生视频', 'includes')).toBe(0);
  });

  it('按钮上现在的值：16:9 720P 1 里有 16:9；1080p · 5s 里有 5s；不会把 15s 当 5s', () => {
    expect(showsValue('16:9 720P 1', '16:9')).toBe(true);
    expect(showsValue('16:9 720P 1', '9:16')).toBe(false);
    expect(showsValue('1080p · 5s', ['5s'])).toBe(true);
    expect(showsValue('15s', ['5s'])).toBe(false);
    expect(showsValue('Seedance 2.0 mini', 'Seedance 2.0')).toBe(false);
    expect(showsValue('视频生成', ['Video generation', '视频生成'])).toBe(true);
  });
});

describe('runGenerator（即梦）', () => {
  it('打开生成页、切到视频生成、选模型 / 首尾帧 / 画幅、填时长、传首帧尾帧、输提示词、等新卡片出视频、取原视频', async () => {
    const page = new FakeGen(JIMENG_CN).afterSubmit(done(60));
    const { out, logs, prog } = await run(page, {
      refs: [FIRST, END], generator: { model: 'Seedance 2.0', ratio: '9:16', duration: 10, end_frame: true },
    });
    expect(out).toEqual([{ src: VID, mime: 'video/mp4', b64: MP4_B64, via: 'page' }]);
    expect(page.calls[0]).toEqual(['goto', JIMENG_CN.generate_url]);
    const picks = page.calls.filter((c) => c[0] === 'pick').map((c) => `${c[1]}=${c[2]}`);
    expect(picks).toEqual(['创作类型=视频生成', '模型=Seedance 2.0', '参考方式=首尾帧', '画幅=9:16']);
    expect(page.calls).toContainEqual(['fill', '10']);
    expect(page.triggers['button.lv-btn']).toEqual(['9:16 720P 1', '10s']);
    expect(page.calls.filter((c) => c[0] === 'setFilesAt')).toEqual([
      ['setFilesAt', 0, ['first.png']], ['setFilesAt', 1, ['end.png']],
    ]);
    expect(page.prompt).toBe('女孩 回头 微笑'); // 一行
    expect(page.calls).toContainEqual(['fetch', VID]);
    expect(page.calls).not.toContainEqual(['fetch', 'https://old/video.mp4']); // 之前的卡片不算
    expect(page.t - page.submittedAt).toBeGreaterThanOrEqual(60 + JIMENG_CN.settle_seconds);
    expect(logs.some((m) => m.includes('积分余额 120，这次要 30'))).toBe(true);
    expect(logs.some((m) => m.startsWith('等了 30 秒：结果区 2 张卡片，新的 1 张，最新一张还没视频'))).toBe(true);
    expect(prog[0]).toEqual([0, 100]);
    expect(prog.at(-1)).toEqual([100, 100]);
  });

  it('页面上已经是要的值就不点；不要尾帧时不传第二张；时长没有输入框时点刻度', async () => {
    const menus = { ...JIMENG_MENUS, 时长: ['4s', '5s', '10s', '15s'].map((h) => opt(h)) };
    const page = new FakeGen(JIMENG_CN, { menus, durationInput: false }).afterSubmit(done(20));
    page.triggers[JIMENG_CN.options[0].open] = ['视频生成', 'Seedance 2.0 mini', '首尾帧'];
    page.triggers['button.lv-btn'] = ['9:16 720P 1', '5s'];
    const { logs } = await run(page, { refs: [FIRST, END], generator: { duration: 15 } });
    const picks = page.calls.filter((c) => c[0] === 'pick').map((c) => `${c[1]}=${c[2]}`);
    expect(picks).toEqual(['时长=15s']);
    expect(logs).toContain('即梦：创作类型已经是 视频生成');
    expect(page.files).toEqual(['first.png']);
  });

  it('没登录（页面上有登录按钮）：报 not_logged_in，不提交', async () => {
    const page = new FakeGen(JIMENG_CN, { loggedIn: false });
    const e = await fails(run(page));
    expect(e).toBeInstanceOf(FlowError);
    expect(e.kind).toBe('not_logged_in');
    expect(e.message).toContain('jimeng.jianying.com');
    expect(page.calls).not.toContainEqual(['click', JIMENG_CN.send_button]);
  });

  it('积分余额比这次要花的少：报 quota，不点生成', async () => {
    const page = new FakeGen(JIMENG_CN, { credits: '0', cost: '30' });
    const e = await fails(run(page));
    expect(e.kind).toBe('quota');
    expect(e.message).toContain('余额 0，这次要 30');
    expect(page.calls).not.toContainEqual(['click', JIMENG_CN.send_button]);
  });

  it('点了生成后提示「积分不足」：报 quota', async () => {
    const page = new FakeGen(JIMENG_CN, { credits: '' }).afterSubmit([[2, (p) => { p.alert = '积分不足，请充值'; }]]);
    const e = await fails(run(page));
    expect(e.kind).toBe('quota');
    expect(e.message).toContain('积分不足');
  });

  it('新卡片上写着审核不通过：报 refused', async () => {
    const page = new FakeGen(JIMENG_CN).afterSubmit([
      [3, (p) => p.addCard({ text: '生成中' })],
      [40, (p) => { const c = p.cards_.at(-1); c.text = '生成失败 内容不合规，请修改后重试'; c.failed = true; }],
    ]);
    const e = await fails(run(page));
    expect(e.kind).toBe('refused');
    expect(e.message).toContain('内容不合规');
  });

  it('卡片失败但没说原因：报 no_image', async () => {
    const page = new FakeGen(JIMENG_CN).afterSubmit([
      [3, (p) => p.addCard({ text: '生成中' })],
      [40, (p) => { const c = p.cards_.at(-1); c.text = '生成失败'; }],
    ]);
    const e = await fails(run(page));
    expect(e.kind).toBe('no_image');
  });

  it('一直没出视频：超时报 timeout', async () => {
    const page = new FakeGen(JIMENG_CN).afterSubmit([[3, (p) => p.addCard({ text: '排队中' })]]);
    const e = await fails(run(page, { timeout: 120 }));
    expect(e.kind).toBe('timeout');
    expect(e.message).toContain('120 秒');
  });

  it('点了生成结果区一直没有新卡片：报 page（可能没提交上或 result_card 变了）', async () => {
    const page = new FakeGen(JIMENG_CN);
    const e = await fails(run(page));
    expect(e.kind).toBe('page');
    expect(e.message).toContain(`${CARD_APPEAR} 秒`);
    expect(e.message).toContain('result_card');
  });

  it('要的模型页面上没有：报 page，列出页面上的可选项', async () => {
    const page = new FakeGen(JIMENG_CN);
    const e = await fails(run(page, { generator: { model: 'Seedance 9.0' } }));
    expect(e.kind).toBe('page');
    expect(e.message).toContain('「模型」里没有「Seedance 9.0」');
    expect(e.message).toContain('Seedance 2.0 mini、Seedance 2.0 Fast VIP');
    expect(page.calls).not.toContainEqual(['click', JIMENG_CN.send_button]);
  });

  it('要的模型是灰的：报 page，说可能要会员', async () => {
    const page = new FakeGen(JIMENG_CN);
    const e = await fails(run(page, { generator: { model: 'Seedance 1.0' } }));
    expect(e.kind).toBe('page');
    expect(e.message).toContain('现在是灰的');
  });

  it('首帧塞进上传框但页面没认：报 page 且不提交', async () => {
    const page = new FakeGen(JIMENG_CN, { fileInputWorks: false });
    const e = await fails(run(page));
    expect(e.kind).toBe('page');
    expect(e.message).toContain('首帧没传上去');
    expect(page.calls).not.toContainEqual(['click', JIMENG_CN.send_button]);
  });

  it('取消：抛 Canceled', async () => {
    const page = new FakeGen(JIMENG_CN).afterSubmit([[3, (p) => p.addCard({ text: '排队中' })]]);
    let n = 0;
    const e = await fails(run(page, { canceled: () => page.submittedAt != null && ++n > 3 }));
    expect(e).toBeInstanceOf(Canceled);
  });
});

describe('runGenerator（可灵）', () => {
  const triggers = { '.model-type-select': ['VIDEO 3.0 Omni'], '.setting-select': ['1080p · 5s'] };
  const menus = {
    生成方式: ['Elements', 'Image to Video', 'Transformation'].map((h) => opt(h)),
    画幅: ['16:9', '9:16', '1:1'].map((h) => opt(h)),
    时长: ['5s', '10s'].map((h) => opt(h)),
  };

  it('技能条直接点「Image to Video」，设置弹层里选画幅、点 10s，新卡片在最前面', async () => {
    const page = new FakeGen(KLING_GLOBAL, { triggers, menus, credits: '' }).afterSubmit([
      [3, (p) => p.addCard({ key: 'task-2', text: 'Generating' })],
      [50, (p) => { p.cards_[0].video = VID; }],
    ]);
    page.cards_[0].key = 'task-1';
    const { out } = await run(page, { spec: KLING_GLOBAL, generator: { ratio: '9:16', duration: 10 } });
    expect(out[0].src).toBe(VID);
    const picks = page.calls.filter((c) => c[0] === 'pick').map((c) => `${c[1]}=${c[2]}`);
    expect(picks).toEqual(['生成方式=Image to Video', '画幅=9:16', '时长=10s']);
    expect(page.files).toEqual(['first.png']);
  });

  it('请求带尾帧但可灵还没配尾帧上传位：报 page，不提交', async () => {
    const page = new FakeGen(KLING_CN, { triggers, menus: { ...menus, 生成方式: [opt('图生视频')] }, credits: '' });
    const e = await fails(run(page, { spec: KLING_CN, refs: [FIRST, END], generator: { end_frame: true } }));
    expect(e.kind).toBe('page');
    expect(e.message).toContain('不支持尾帧');
    expect(page.calls).not.toContainEqual(['click', KLING_CN.send_button]);
  });
});

describe('生成器网站登记', () => {
  it('四个区域各一个 key，kind = generator，扩展报 generator 能力；不在聊天页的 VIDEO_SITES 里', () => {
    expect(GENERATOR_SITES).toEqual(['jimeng_cn', 'jimeng_global', 'kling_cn', 'kling_global']);
    for (const k of GENERATOR_SITES) {
      expect(SITES[k].kind).toBe('generator');
      expect(SITES[k].generate_url.startsWith(`https://${SITES[k].host}/`)).toBe(true);
    }
    expect(VIDEO_SITES).not.toContain('jimeng_cn');
    expect(FEATURES).toContain('generator');
    expect(JIMENG_GLOBAL.host).toBe('dreamina.capcut.com');
    expect(KLING_CN.host).toBe('klingai.com');
  });
});

describe('content/dom.js 生成器页操作', () => {
  let D;
  beforeAll(() => {
    globalThis.__aiStudioDomNoLayout = true;
    D = globalThis.__aiStudioDom;
  });
  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('options：看得见的 Arco 选项，带灰的 / 选中的；clickAt 点第几个', () => {
    document.body.innerHTML = `
      <div role="combobox" class="lv-select" id="c1">视频生成</div>
      <div role="combobox" class="lv-select" id="c2">Seedance 2.0 mini</div>
      <div class="lv-select-popup">
        <div class="lv-select-option-wrapper lv-select-option-wrapper-selected"><div class="lv-select-option">Seedance 2.0 mini</div></div>
        <div class="lv-select-option-wrapper lv-select-option-wrapper-disabled"><div class="lv-select-option">Seedance 1.0</div></div>
        <div class="lv-select-option-wrapper"><div class="lv-select-option" id="o3">Seedance 2.0</div></div>
      </div>
      <div style="display:none"><div class="lv-select-option">藏起来的</div></div>`;
    const triggers = D.options(JIMENG_CN.options[0].open);
    expect(triggers.map((t) => t.text)).toEqual(['视频生成', 'Seedance 2.0 mini']);
    const items = D.options('.lv-select-option');
    expect(items.map((t) => [t.head, t.disabled, t.selected])).toEqual([
      ['Seedance 2.0 mini', false, true], ['Seedance 1.0', true, false], ['Seedance 2.0', false, false],
    ]);
    let clicked = '';
    document.getElementById('o3').addEventListener('click', () => { clicked = 'o3'; });
    expect(D.clickAt('.lv-select-option', 2)).toBe(true);
    expect(clicked).toBe('o3');
    expect(D.clickAt('.lv-select-option', 9)).toBe(false);
  });

  it('fillInput 写数字输入框并发 input / change；setFilesAt 按顺序塞第 2 个上传框', () => {
    document.body.innerHTML = `
      <div class="lv-popover"><div class="lv-input-number"><input id="n" value="5"></div></div>
      <input type="file" id="f1" style="display:none"><input type="file" id="f2" style="display:none">`;
    const seen = [];
    const n = document.getElementById('n');
    n.addEventListener('input', () => seen.push(`input:${n.value}`));
    n.addEventListener('change', () => seen.push(`change:${n.value}`));
    expect(D.fillInput('.lv-popover .lv-input-number', '10')).toBe(true);
    expect(seen).toEqual(['input:10', 'change:10']);
    // jsdom 没有 DataTransfer、input.files 也不能随便赋值：换成最小的替身
    globalThis.DataTransfer ??= class {
      constructor() { this.files = []; this.items = { add: (f) => this.files.push(f) }; }
    };
    for (const el of document.querySelectorAll('input[type="file"]')) {
      Object.defineProperty(el, 'files', { configurable: true, get() { return this._f || []; }, set(v) { this._f = v; } });
    }
    let got = '';
    document.getElementById('f2').addEventListener('change', (e) => { got = e.target.files[0].name; });
    expect(D.setFilesAt('input[type="file"]', 1, [END])).toBe(true);
    expect(got).toBe('end.png');
    expect(D.setFilesAt('input[type="file"]', 5, [END])).toBe(false);
  });

  it('cards：卡片的 id、文字、里面视频的地址、失败标志', () => {
    document.body.innerHTML = `
      <div class="record-item" data-id="r1"><p>已完成</p><video src="https://cdn.example/a.mp4"></video></div>
      <div class="record-item" data-id="r2"><p>生成失败 内容不合规</p><span class="fail-icon"></span></div>
      <div class="record-item"><p>排队中</p><video><source src="https://cdn.example/b.mp4"></video></div>`;
    const cards = D.cards(JIMENG_CN.result_card, JIMENG_CN.result_video, JIMENG_CN.result_failed);
    expect(cards.map((c) => [c.key, c.video, c.failed])).toEqual([
      ['r1', 'https://cdn.example/a.mp4', false], ['r2', '', true], ['', 'https://cdn.example/b.mp4', false],
    ]);
    expect(cards[1].text).toContain('内容不合规');
  });
});
