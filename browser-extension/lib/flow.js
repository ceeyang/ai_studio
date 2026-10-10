/*
 * 通用网页出图流程：新对话 → 传参考图 → 输提示词 → 提交 → 等图出完 → 取原图。
 * 逐条照搬 backend/engines/browser/flow.py（同样的判断、同样的超时、同样的中文原因），只是页面操作换成
 * 扩展的 driver（background.js 里的 TabDriver，单测里是假的），所有方法都是 async。
 *
 * driver 接口：goto(url) url() visible(sel) count(sel) enabled(sel) click(sel) typeText(sel, text)
 *   press(sel, key) setFiles(sel, files) pasteFiles(sel, files) dropFiles(sel, files) text(sel)
 *   images(sel) fetchImage(src) → {mime, b64, via} cookieNames(url) → string[] sleep(seconds)
 * files 是 [{name, mime, b64}]（studio 发来的参考图）。
 *
 * 任何一步不对都抛 FlowError(kind, message)：kind 和后端 BrowserError 一样（not_logged_in / quota /
 * refused / timeout / no_image / page / browser），message 原样显示在任务上，所以要说清楚怎么办。
 */
import { sniffMime } from './protocol.js';

export const PROGRESS_MAX = 100;
/** 等图时每隔多少秒往任务日志写一次诊断（停止按钮、候选图被排除的原因）。 */
export const DIAG_EVERY = 30;

export class FlowError extends Error {
  constructor(kind, message) {
    super(message);
    this.kind = kind;
  }
}

export class Canceled extends Error {
  constructor() {
    super('已取消');
    this.kind = 'canceled';
  }
}

export function excerpt(text, n = 160) {
  const t = String(text || '').split(/\s+/).filter(Boolean).join(' ');
  return t.length <= n ? t : t.slice(0, n - 1) + '…';
}

export function notLoggedIn(spec) {
  return new FlowError(
    'not_logged_in',
    `${spec.label} 没有登录：在你平时用的这个浏览器里打开 ${spec.host} 登录（只需一次），再重试这个任务`,
  );
}

function match(patterns, text) {
  return !!text && patterns.some((p) => p.test(text));
}

function siteFile(spec) {
  return `browser-extension/sites/${spec.key}.js（和 backend/engines/browser/sites/${spec.key}.py）`;
}

class Flow {
  constructor(d, spec, { isCanceled, log, progress, clock, poll }) {
    this.d = d;
    this.spec = spec;
    this.isCanceled = isCanceled || (() => false);
    this.log = log || (() => {});
    this.progress = progress || (() => {});
    this.clock = clock;
    this.poll = poll;
    this.echo = new Set(); // 网站回显的参考图（提交后马上出现的图）
    this.nRefs = 0;
  }

  async checkCancel() {
    if (this.isCanceled()) {
      if (await this.d.visible(this.spec.stop_button)) await this.d.click(this.spec.stop_button);
      throw new Canceled();
    }
  }

  async dismiss() {
    if (this.spec.dismiss_buttons && (await this.d.visible(this.spec.dismiss_buttons))) {
      await this.d.click(this.spec.dismiss_buttons);
    }
  }

  async loggedInByCookie() {
    const names = new Set(await this.d.cookieNames(this.spec.cookie_url));
    return this.spec.session_cookies.some((c) => names.has(c));
  }

  // ------------------------------------------------------------ 步骤
  async openChat({ reuseConversation = false, fresh = false } = {}) {
    const s = this.spec;
    const here = String((await this.d.url()) || '');
    const onSite = s.hosts.some((h) => here.includes(`://${h}/`));
    if (!(fresh && onSite) && !(reuseConversation && onSite)) {
      await this.d.goto(s.new_chat_url);
    }
    const start = this.clock();
    let warned = false;
    for (;;) {
      await this.checkCancel();
      const at = String((await this.d.url()) || '');
      if ((s.login_hosts || []).some((h) => at.includes(`://${h}/`))) throw notLoggedIn(s);
      if (await this.d.visible(s.login_wall)) throw notLoggedIn(s);
      if (await this.d.visible(s.prompt_box)) {
        // cookie 刚写进去时可能慢一拍：输入框都出来了还没 cookie，再等一轮（同 flow.py 的 5 秒）
        if ((await this.loggedInByCookie()) || this.clock() - start > 5) {
          if (!(await this.loggedInByCookie()) && !warned) {
            warned = true;
            this.log(`${s.label}：没读到登录 cookie，但页面上没有登录按钮，按已登录继续`);
          }
          await this.dismiss();
          return;
        }
      }
      if (this.clock() - start > s.ready_timeout) {
        throw new FlowError(
          'page',
          `${s.label} 页面 ${Math.round(s.ready_timeout)} 秒内没出现输入框（当前地址 ${await this.d.url()}）。` +
            `网络不通，或网站改版了——要更新 ${siteFile(s)} 里的 prompt_box`,
        );
      }
      await this.d.sleep(this.poll);
    }
  }

  async waitAttachments(before, n) {
    const s = this.spec;
    const start = this.clock();
    for (;;) {
      await this.checkCancel();
      await this.dismiss();
      const got = (await this.d.count(s.attachment)) - before;
      const busy = await this.d.visible(s.attachment_busy);
      if (got >= n && !busy) return true;
      const waited = this.clock() - start;
      if (!busy && waited > 15) {
        this.log(`${s.label}：输入框里只看到 ${Math.max(got, 0)}/${n} 张参考图缩略图（也可能是缩略图选择器过时），继续提交`);
        return false;
      }
      if (waited > s.upload_timeout) {
        throw new FlowError('timeout', `${s.label} 参考图 ${Math.round(s.upload_timeout)} 秒还没传完，已放弃`);
      }
      await this.d.sleep(this.poll);
    }
  }

  async upload(refs) {
    const s = this.spec;
    if (!refs || !refs.length) return;
    this.nRefs = refs.length;
    this.log(`${s.label}：上传 ${refs.length} 张参考图`);
    const before = await this.d.count(s.attachment);
    let ok = await this.d.setFiles(s.file_input, refs);
    if (!ok && s.upload_buttons.length > 1) {
      // 点开「+」之类的菜单（最后一个按钮会弹文件选择框，扩展替不了用户选文件，不点）
      for (const sel of s.upload_buttons.slice(0, -1)) {
        if (await this.d.click(sel)) await this.d.sleep(0.6);
      }
      ok = await this.d.setFiles(s.file_input, refs);
    }
    if (ok) {
      await this.waitAttachments(before, refs.length);
      return;
    }
    // 没有 file input：粘贴图片，几秒内没出缩略图再试拖放
    if (await this.d.pasteFiles(s.prompt_box, refs)) {
      const start = this.clock();
      while (this.clock() - start < 6) {
        await this.checkCancel();
        if ((await this.d.count(s.attachment)) > before || (await this.d.visible(s.attachment_busy))) {
          await this.waitAttachments(before, refs.length);
          return;
        }
        await this.d.sleep(this.poll);
      }
      if (await this.d.dropFiles(s.prompt_box, refs)) {
        await this.waitAttachments(before, refs.length);
        return;
      }
    }
    throw new FlowError(
      'page',
      `${s.label} 上传参考图失败：没找到上传入口，粘贴也没反应。网站改版了，要更新 ${siteFile(s)} 里的 file_input / upload_buttons`,
    );
  }

  async submit(prompt) {
    const s = this.spec;
    if (!(await this.d.typeText(s.prompt_box, prompt))) {
      throw new FlowError('page', `${s.label} 没能把提示词输进输入框。网站改版了，要更新 ${siteFile(s)} 里的 prompt_box`);
    }
    const start = this.clock();
    while (!(await this.d.enabled(s.send_button))) {
      // 参考图还在处理时发送按钮是灰的
      await this.checkCancel();
      if (this.clock() - start > 30) break;
      await this.d.sleep(this.poll);
    }
    if (!(await this.d.click(s.send_button)) && !(await this.d.press(s.prompt_box, 'Enter'))) {
      throw new FlowError('page', `${s.label} 没能点发送。网站改版了，要更新 ${siteFile(s)} 里的 send_button`);
    }
  }

  async results(why) {
    const s = this.spec;
    const mine = new Set((await this.d.images(s.user_images)).map((i) => i.src));
    const out = new Map();
    // why（可选数组）：记下每张候选图被排除的原因，等图久了写进任务日志，方便排查选择器
    const skip = (i, reason) => {
      if (why) why.push(`${reason} ${i.w}x${i.h} ${String(i.src).slice(0, 80)}`);
    };
    for (const i of await this.d.images(s.result_images)) {
      if (!i.src) { skip(i, '无地址'); continue; }
      if (mine.has(i.src)) { skip(i, '是用户上传的图'); continue; }
      if (this.echo.has(i.src)) { skip(i, '是回显的参考图'); continue; }
      if (out.has(i.src)) continue;
      if (!i.complete) { skip(i, '还没加载完'); continue; }
      if (Math.max(i.w, i.h) < s.min_image_px) { skip(i, '太小'); continue; }
      if (s.image_src_hint.length && !s.image_src_hint.some((h) => i.src.includes(h))) { skip(i, '地址不像出图结果'); continue; }
      out.set(i.src, i);
    }
    return [...out.values()];
  }

  async waitImages(timeout) {
    const s = this.spec;
    const start = this.clock();
    let last = null;
    let stableSince = 0;
    let idleSince = 0;
    let nextDiag = DIAG_EVERY;
    for (;;) {
      await this.checkCancel();
      const now = this.clock();
      const elapsed = now - start;
      if (elapsed >= nextDiag) {
        nextDiag += DIAG_EVERY;
        await this.diagnose(elapsed);
      }
      if (elapsed > timeout) {
        if (await this.d.visible(s.stop_button)) await this.d.click(s.stop_button);
        throw new FlowError(
          'timeout',
          `${s.label} 超过 ${Math.round(timeout)} 秒还没出完图，已放弃（浏览器里那条对话还在，可以去看看卡在哪，再重试）`,
        );
      }
      const reply = await this.d.text(s.assistant_turn);
      const alert = s.alert ? await this.d.text(s.alert) : '';
      for (const t of [alert, reply]) {
        if (match(s.quota_patterns, t)) {
          throw new FlowError('quota', `${s.label} 出图次数到上限或被限流：${excerpt(t)}。等一阵再重试，或换别的图像引擎`);
        }
      }
      if (await this.d.visible(s.login_wall)) throw notLoggedIn(s);
      const busy = await this.isBusy();
      let imgs = await this.results();
      if (this.nRefs && elapsed <= s.echo_seconds && imgs.length) {
        // 生成的图不可能几秒内就出来：这时冒出来的是网站回显的参考图
        for (const i of imgs) this.echo.add(i.src);
        imgs = [];
      }
      const srcs = imgs.map((i) => i.src).join('\n');
      if (srcs && !busy) {
        if (srcs !== last) {
          last = srcs;
          stableSince = now;
        } else if (now - stableSince >= s.settle_seconds) {
          return imgs;
        }
      } else {
        last = null;
      }
      if (!busy && !srcs && reply.trim()) {
        idleSince = idleSince || now;
        if (now - idleSince >= s.no_image_grace) {
          if (match(s.refusal_patterns, reply)) {
            throw new FlowError('refused', `${s.label} 拒绝了这次出图（内容审核）：${excerpt(reply)}。改一下提示词再试`);
          }
          throw new FlowError('no_image', `${s.label} 回复了文字但没有出图：${excerpt(reply)}`);
        }
      } else {
        idleSince = 0;
      }
      this.progress(Math.min(95, Math.floor((elapsed / Math.max(s.expected_seconds, 1)) * 95)), PROGRESS_MAX);
      await this.d.sleep(this.poll);
    }
  }

  /** 还在出图：停止按钮看得见，或者网站标着这一轮没完成（busy_marker）。 */
  async isBusy() {
    const s = this.spec;
    if (await this.d.visible(s.stop_button)) return true;
    return !!s.busy_marker && (await this.d.visible(s.busy_marker));
  }

  /** 等图超过 DIAG_EVERY 秒还没结束：把判断依据写进任务日志（停止按钮在不在、候选图各自为什么不算）。 */
  async diagnose(elapsed) {
    const s = this.spec;
    const why = [];
    const busy = await this.isBusy();
    const got = await this.results(why);
    const all = await this.d.count(s.result_images);
    const parts = [
      `等了 ${Math.round(elapsed)} 秒：停止按钮${busy ? '还在' : '已消失'}`,
      `选择器命中 ${all} 张图，可用 ${got.length} 张`,
    ];
    if (why.length) parts.push(`排除：${why.slice(0, 4).join('；')}`);
    this.log(parts.join('，'));
  }

  async download(imgs) {
    const s = this.spec;
    const out = [];
    const errors = [];
    for (const info of imgs) {
      await this.checkCancel();
      for (const src of s.fullSize(info.src)) {
        let got;
        try {
          got = await this.d.fetchImage(src);
        } catch (e) {
          errors.push(`${excerpt(src, 60)}：${(e && e.message) || e}`);
          continue;
        }
        const mime = sniffMime(got && got.b64);
        if (!mime) {
          errors.push(`${excerpt(src, 60)}：取回来的不是图片（${(got && got.mime) || '未知类型'}）`);
          continue;
        }
        out.push({ src, mime, b64: got.b64, via: got.via || 'page' });
        break;
      }
    }
    if (!out.length) {
      throw new FlowError('page', `${s.label} 出了图但取原图失败：${errors.join('；').slice(0, 300)}`);
    }
    return out;
  }
}

/**
 * 跑一次出图。job: {prompt, refs: [{name, mime, b64}], timeout, reuseConversation, fresh}
 * 返回 [{src, mime, b64, via}]。
 */
export async function generate(d, spec, job, { isCanceled, log, progress, clock, poll = 1 } = {}) {
  const lg = log || (() => {});
  const pg = progress || (() => {});
  const clk = clock || (() => Date.now() / 1000);
  const f = new Flow(d, spec, { isCanceled, log: lg, progress: pg, clock: clk, poll });
  pg(0, PROGRESS_MAX);
  lg(`${spec.label}：打开新对话`);
  await f.openChat({ reuseConversation: !!job.reuseConversation, fresh: !!job.fresh });
  await f.upload(job.refs || []);
  await f.submit(job.prompt);
  const timeout = Number(job.timeout) || 300;
  lg(`${spec.label}：已提交，等出图（最长 ${Math.round(timeout)} 秒）`);
  const t0 = clk();
  const imgs = await f.waitImages(timeout);
  lg(`${spec.label}：出了 ${imgs.length} 张图，用时 ${Math.round(clk() - t0)} 秒，取原图`);
  pg(PROGRESS_MAX, PROGRESS_MAX);
  return f.download(imgs);
}
