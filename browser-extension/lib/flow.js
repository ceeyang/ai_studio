/*
 * 通用网页出图流程：新对话（或续接之前的对话）→ 传参考图 → 输提示词 → 提交 → 等图出完 → 取原图。
 * 出视频（v0.4 起，job.kind = 'video'，只有扩展这边有）：同样打开对话 → 选做视频的工具和画幅 → 传首帧 →
 * 提交 → 等 <video> 出完 → 在页面里取原视频（mp4）。
 * 逐条照搬 backend/engines/browser/flow.py（同样的判断、同样的超时、同样的中文原因），只是页面操作换成
 * 扩展的 driver（background.js 里的 TabDriver，单测里是假的），所有方法都是 async。
 *
 * driver 接口：goto(url) url() visible(sel) count(sel) enabled(sel) click(sel) typeText(sel, text)
 *   press(sel, key) setFiles(sel, files) pasteFiles(sel, files) dropFiles(sel, files) text(sel)
 *   images(sel) videos(sel) fetchImage(src) → {mime, b64, via} cookieNames(url) → string[] sleep(seconds)
 * files 是 [{name, mime, b64}]（studio 发来的参考图）。
 *
 * 任何一步不对都抛 FlowError(kind, message)：kind 和后端 BrowserError 一样（not_logged_in / quota /
 * refused / timeout / no_image / page / browser），message 原样显示在任务上，所以要说清楚怎么办。
 */
import { sniffMime } from './protocol.js';

export const PROGRESS_MAX = 100;
/** 等图时每隔多少秒往任务日志写一次诊断（停止按钮、候选图被排除的原因）。 */
export const DIAG_EVERY = 30;
/** 打开之前的对话后最多等这么多秒看到之前的回复，看不到就当这条对话没了（同 flow.py 的 CONTINUE_CHECK）。 */
export const CONTINUE_CHECK = 15;
/** 续接时等页面上已有的图加载稳定（再记「之前的图」）最多这么多秒（同 flow.py 的 SNAPSHOT_WAIT）。 */
export const SNAPSHOT_WAIT = 10;

/** 网址是这个网站上的一条对话时返回对话 id，否则 ''（首页、新对话页、登录页、别的网站）。 */
export function conversationId(spec, url) {
  if (!url || !spec.conversation_re) return '';
  let u;
  try {
    u = new URL(url);
  } catch (_e) {
    return '';
  }
  const host = u.hostname.toLowerCase();
  if (!(spec.hosts || [spec.host]).some((h) => host === h || host.endsWith('.' + h))) return '';
  const m = new RegExp(spec.conversation_re).exec(u.pathname || '');
  return m ? m[1] : '';
}

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

const WHAT = { image: '图', video: '视频' };

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
    this.continued = false; // 在之前的对话里接着发
    this.before = new Set(); // 提交前页面上就有的图 / 视频（之前几轮的结果、参考图）
    this.beforeReply = ''; // 提交前最后一条回复的文字
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

  /** 打开之前的对话：打得开（地址还是那条、输入框出来了、看得到之前的回复）返回 ''，打不开返回中文原因。 */
  async openExisting(url) {
    const s = this.spec;
    const want = conversationId(s, url);
    await this.d.goto(url);
    let start = this.clock();
    for (;;) {
      await this.checkCancel();
      const here = String((await this.d.url()) || '');
      if ((s.login_hosts || []).some((h) => here.includes(`://${h}/`))) throw notLoggedIn(s);
      if (await this.d.visible(s.login_wall)) throw notLoggedIn(s);
      if (want && conversationId(s, here) !== want) return `会话打不开（跳到了 ${here || '空白页'}，可能已被删除）`;
      if (await this.d.visible(s.prompt_box)) break;
      if (this.clock() - start > s.ready_timeout) {
        return `会话页面 ${Math.round(s.ready_timeout)} 秒内没出现输入框（当前地址 ${here}）`;
      }
      await this.d.sleep(this.poll);
    }
    start = this.clock();
    while ((await this.d.count(s.assistant_turn)) <= 0) {
      // 之前的回复要在页面上：一条都看不到说明这条对话已经没了（或是空的）
      await this.checkCancel();
      if (this.clock() - start > CONTINUE_CHECK) return '会话里看不到之前的回复（可能已被删除或清空）';
      await this.d.sleep(this.poll);
    }
    await this.dismiss();
    this.continued = true;
    return '';
  }

  /** 提交前记下页面上已有的图和最后一条回复；续接时先等已有的图加载稳定。 */
  async snapshot() {
    const s = this.spec;
    const start = this.clock();
    let last = null;
    let srcs = [];
    for (;;) {
      const all = [...(await this.d.images(s.result_images)), ...(await this.d.images(s.user_images)),
        ...(s.result_videos ? await this.d.videos(s.result_videos) : [])];
      srcs = [...new Set(all.map((i) => i.src).filter(Boolean))].sort();
      const key = srcs.join('\n');
      if (!this.continued || key === last || this.clock() - start > SNAPSHOT_WAIT) break;
      last = key;
      await this.d.sleep(this.poll);
    }
    this.before = new Set(srcs);
    this.beforeReply = this.continued ? await this.d.text(s.assistant_turn, s.reply_exclude) : '';
  }

  /** 出视频：选中网站的做视频工具（已经选中就不点），第一次弹的介绍框点掉，再在画幅菜单里选画幅。 */
  async selectVideoTool(aspect) {
    const s = this.spec;
    const steps = s.video_steps || [];
    if (steps.length && !(s.video_active && (await this.d.visible(s.video_active)))) {
      for (const sel of steps) {
        await this.checkCancel();
        if (!(await this.d.click(sel))) {
          throw new FlowError(
            'page',
            `${s.label} 没找到做视频的按钮（${excerpt(sel, 60)}）。网站改版了，要更新 ${siteFile(s)} 里的 video_steps`,
          );
        }
        await this.d.sleep(1);
      }
      for (let i = 0; i < 3 && s.video_dismiss && (await this.d.visible(s.video_dismiss)); i++) {
        await this.d.click(s.video_dismiss); // 第一次用视频工具时的介绍框
        await this.d.sleep(1);
      }
      if (s.video_active && !(await this.d.visible(s.video_active))) {
        throw new FlowError(
          'page',
          `${s.label} 点了做视频的按钮但没进入视频模式（可能这个账号没有视频额度，或网站改版了——` +
            `要更新 ${siteFile(s)} 里的 video_steps / video_active）`,
        );
      }
      this.log(`${s.label}：已切到做视频`);
    }
    const opt = (s.video_aspect_options || {})[aspect];
    if (s.video_aspect_button && opt) {
      if (await this.d.click(s.video_aspect_button)) {
        await this.d.sleep(0.8);
        if (await this.d.click(opt)) {
          this.log(`${s.label}：画幅选了 ${aspect}`);
          await this.d.sleep(0.5);
          return;
        }
      }
      this.log(`${s.label}：没能在画幅菜单里选 ${aspect}，按网站当前的画幅出（提示词里也写了画幅）`);
    }
  }

  /** 出图：标签页还停在做视频的模式（上一个任务留下的）就先退出来，不然会把出图请求做成视频。 */
  async leaveVideoTool() {
    const s = this.spec;
    if (s.video_active && (await this.d.visible(s.video_active))) {
      await this.d.click(s.video_active);
      await this.d.sleep(0.5);
      this.log(`${s.label}：退出了做视频的模式`);
    }
  }

  /** 输入框里的参考图缩略图数：缩略图选择器和「输入框那一块里的图片数」取大的，网站改版也数得出来。 */
  async attachCount() {
    const s = this.spec;
    return Math.max(await this.d.count(s.attachment), await this.d.composerImages(s.prompt_box));
  }

  /** 等参考图出现在输入框里：secs 秒内一点动静都没有（没缩略图也没在上传）就算这种方式没用。 */
  async attached(before, n, secs) {
    const s = this.spec;
    const start = this.clock();
    for (;;) {
      await this.checkCancel();
      await this.dismiss();
      const got = (await this.attachCount()) - before;
      const busy = await this.d.visible(s.attachment_busy);
      if (got >= n && !busy) return true;
      const waited = this.clock() - start;
      if (!busy && got <= 0 && waited > secs) return false;
      if (waited > s.upload_timeout) {
        if (got > 0) {
          this.log(`${s.label}：只看到 ${got}/${n} 张参考图缩略图，按已传上的继续`);
          return true;
        }
        throw new FlowError('timeout', `${s.label} 参考图 ${Math.round(s.upload_timeout)} 秒还没传完，已放弃`);
      }
      await this.d.sleep(this.poll);
    }
  }

  /** 传参考图：依次试 上传框 → 粘贴 → 拖放，每种都确认输入框里真出现了缩略图；都不行就报错、不提交
   *  （不带参考图发出去，网站要么出一张不像的图，要么回一句「请上传参考图」，白等一场）。 */
  async upload(refs) {
    const s = this.spec;
    if (!refs || !refs.length) return;
    this.nRefs = refs.length;
    const n = refs.length;
    this.log(`${s.label}：上传 ${n} 张参考图`);
    const before = await this.attachCount();
    const tried = [];
    const done = (how) => this.log(`${s.label}：参考图已传上（${how}）`);
    let ok = await this.d.setFiles(s.file_input, refs, s.prompt_box);
    if (!ok && s.upload_buttons.length > 1) {
      // 点开「+」之类的菜单（最后一个按钮会弹文件选择框，扩展替不了用户选文件，不点）
      for (const sel of s.upload_buttons.slice(0, -1)) {
        if (await this.d.click(sel)) await this.d.sleep(0.6);
      }
      ok = await this.d.setFiles(s.file_input, refs, s.prompt_box);
    }
    if (ok) {
      tried.push('上传框');
      if (await this.attached(before, n, 15)) return done('上传框');
    }
    if (await this.d.pasteFiles(s.prompt_box, refs)) {
      tried.push('粘贴');
      if (await this.attached(before, n, 8)) return done('粘贴');
    }
    if (await this.d.dropFiles(s.prompt_box, refs)) {
      tried.push('拖放');
      if (await this.attached(before, n, 8)) return done('拖放');
    }
    const fi = await this.d.fileInputs(s.file_input, s.prompt_box);
    throw new FlowError(
      'page',
      `${s.label} 参考图没传上去（试过：${tried.join('、') || '没找到可用的方式'}；页面上有 ${fi.total} 个图片上传框，` +
        `输入框旁边 ${fi.near} 个），这次没有提交。网站可能改版了，要更新 ${siteFile(s)} 里的 file_input / attachment`,
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
    const seen = new Set();
    // why（可选数组）：记下每张候选图被排除的原因，等图久了写进任务日志，方便排查选择器
    const skip = (i, reason) => {
      if (why) why.push(`${reason} ${i.w}x${i.h} ${String(i.src).slice(0, 80)}`);
    };
    for (const i of await this.d.images(s.result_images)) {
      if (!i.src) { skip(i, '无地址'); continue; }
      if (mine.has(i.src)) { skip(i, '是用户上传的图'); continue; }
      if (this.before.has(i.src)) { skip(i, '是之前几轮的图'); continue; }
      if (this.echo.has(i.src)) { skip(i, '是回显的参考图'); continue; }
      if (out.has(i.src)) continue;
      if (!i.complete) { skip(i, '还没加载完'); continue; }
      if (Math.max(i.w, i.h) < s.min_image_px) { skip(i, '太小'); continue; }
      if (s.image_src_hint.length && !s.image_src_hint.some((h) => i.src.includes(h))) { skip(i, '地址不像出图结果'); continue; }
      // 同一张图网站会再放一个副本（地址不同）：alt+尺寸相同只留第一张
      const key = i.alt ? `${i.alt}|${i.w}x${i.h}` : '';
      if (key && seen.has(key)) continue;
      if (key) seen.add(key);
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
      let reply = await this.d.text(s.assistant_turn, s.reply_exclude);
      if (this.continued && reply === this.beforeReply) reply = ''; // 还是上一轮的回复，这次的还没出来
      const alert = s.alert ? await this.d.text(s.alert) : '';
      for (const t of [alert, reply]) {
        if (match(s.quota_patterns, t)) {
          throw new FlowError('quota', `${s.label} 出图次数到上限或被限流：${excerpt(t)}。等一阵再重试，或换别的图像引擎`);
        }
      }
      if (await this.d.visible(s.login_wall)) throw notLoggedIn(s);
      const busy = await this.isBusy();
      let imgs = await this.results();
      if ((this.nRefs || this.continued) && elapsed <= s.echo_seconds && imgs.length) {
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

  /** 新出的视频：不是提交前就有的、地址像生成结果，同一地址只留一个（尺寸、时长后台标签页里常读不到，不判断）。 */
  async videoResults(why) {
    const s = this.spec;
    const out = new Map();
    for (const v of await this.d.videos(s.result_videos)) {
      if (!v.src) { if (why) why.push('无地址'); continue; }
      if (this.before.has(v.src)) { if (why) why.push('是之前几轮的视频'); continue; }
      if (out.has(v.src)) continue;
      if (s.video_src_hint.length && !s.video_src_hint.some((h) => v.src.includes(h))) {
        if (why) why.push(`地址不像生成结果 ${String(v.src).slice(0, 80)}`);
        continue;
      }
      out.set(v.src, v);
    }
    return [...out.values()];
  }

  /** 等视频：判断和等图一样（限额、登录、拒绝、地址稳定 settle 秒），只是看 <video>；回复里说「正在生成，
   *  要几分钟」（video_pending_patterns）时不算「回复了文字但没出视频」。 */
  async waitVideos(timeout) {
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
        const why = [];
        const got = await this.videoResults(why);
        const all = (await this.d.videos(s.result_videos)).length;
        this.log([`等了 ${Math.round(elapsed)} 秒：停止按钮${(await this.isBusy()) ? '还在' : '已消失'}`,
          `选择器命中 ${all} 个视频，可用 ${got.length} 个`, ...(why.length ? [`排除：${why.slice(0, 4).join('；')}`] : [])]
          .join('，'));
      }
      if (elapsed > timeout) {
        if (await this.d.visible(s.stop_button)) await this.d.click(s.stop_button);
        throw new FlowError(
          'timeout',
          `${s.label} 超过 ${Math.round(timeout)} 秒还没出完视频，已放弃（浏览器里那条对话还在，视频可能稍后出来，可以去看看）`,
        );
      }
      let reply = await this.d.text(s.assistant_turn, s.reply_exclude);
      if (this.continued && reply === this.beforeReply) reply = ''; // 还是上一轮的回复，这次的还没出来
      const alert = s.alert ? await this.d.text(s.alert) : '';
      for (const t of [alert, reply]) {
        if (match(s.quota_patterns, t)) {
          throw new FlowError('quota', `${s.label} 出视频次数到上限或被限流：${excerpt(t)}。等一阵再重试，或换别的视频引擎`);
        }
      }
      if (await this.d.visible(s.login_wall)) throw notLoggedIn(s);
      const busy = await this.isBusy();
      const vids = await this.videoResults();
      const srcs = vids.map((v) => v.src).join('\n');
      if (srcs && !busy) {
        if (srcs !== last) {
          last = srcs;
          stableSince = now;
        } else if (now - stableSince >= s.settle_seconds) {
          return vids;
        }
      } else {
        last = null;
      }
      const pending = match(s.video_pending_patterns, reply);
      if (!busy && !srcs && reply.trim() && !pending) {
        idleSince = idleSince || now;
        if (now - idleSince >= s.no_image_grace) {
          if (match(s.refusal_patterns, reply)) {
            throw new FlowError('refused', `${s.label} 拒绝了这次出视频（内容审核）：${excerpt(reply)}。改一下提示词再试`);
          }
          throw new FlowError('no_image', `${s.label} 回复了文字但没有出视频：${excerpt(reply)}`);
        }
      } else {
        idleSince = 0;
      }
      this.progress(Math.min(95, Math.floor((elapsed / Math.max(s.video_expected_seconds, 1)) * 95)), PROGRESS_MAX);
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

  async download(imgs, kind = 'image') {
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
        if (!mime || !mime.startsWith(`${kind}/`)) {
          const want = kind === 'video' ? '视频' : '图片';
          errors.push(`${excerpt(src, 60)}：取回来的不是${want}（${mime || (got && got.mime) || '未知类型'}）`);
          continue;
        }
        out.push({ src, mime, b64: got.b64, via: got.via || 'page' });
        break;
      }
    }
    if (!out.length) {
      const what = kind === 'video' ? '视频但取原视频' : '图但取原图';
      throw new FlowError('page', `${s.label} 出了${what}失败：${errors.join('；').slice(0, 300)}`);
    }
    return out;
  }
}

/**
 * 跑一次出图 / 出视频。job: {kind: 'image'|'video', prompt, refs: [{name, mime, b64}], aspect, timeout,
 *   reuseConversation, fresh, conversationUrl}（kind = video 时 refs 里是首帧）
 * conversationUrl（v0.3 起）：在这条之前的对话里接着出图，打不开就新开。
 * 返回 [{src, mime, b64, via}]；onConversation（可选）收到 {url, continued, note}：结束时标签页地址、
 * 是否真的续接上了、退回新对话的原因。
 */
export async function generate(d, spec, job, { isCanceled, log, progress, clock, poll = 1, onConversation } = {}) {
  const lg = log || (() => {});
  const pg = progress || (() => {});
  const clk = clock || (() => Date.now() / 1000);
  const kind = job.kind === 'video' ? 'video' : 'image';
  if (kind === 'video' && !spec.result_videos) {
    throw new FlowError('browser', `${spec.label} 不能出视频（扩展里没有它的视频选择器）`);
  }
  const f = new Flow(d, spec, { isCanceled, log: lg, progress: pg, clock: clk, poll });
  pg(0, PROGRESS_MAX);
  let note = '';
  if (job.conversationUrl) {
    lg(`${spec.label}：打开之前的会话 ${job.conversationUrl}`);
    note = await f.openExisting(job.conversationUrl);
    if (note) lg(`${spec.label}：${note}，新开对话`);
  }
  if (!f.continued) {
    lg(`${spec.label}：打开新对话`);
    // 续接失败时标签页停在别的页面上：一定要跳到新对话
    await f.openChat({
      reuseConversation: !job.conversationUrl && !!job.reuseConversation,
      fresh: !job.conversationUrl && !!job.fresh,
    });
  }
  if (kind === 'video') await f.selectVideoTool(job.aspect);
  else await f.leaveVideoTool();
  await f.snapshot();
  await f.upload(job.refs || []);
  await f.submit(job.prompt);
  const timeout = Number(job.timeout) || 300;
  lg(`${spec.label}：已提交，等出${WHAT[kind]}（最长 ${Math.round(timeout)} 秒）`);
  const t0 = clk();
  const items = kind === 'video' ? await f.waitVideos(timeout) : await f.waitImages(timeout);
  const unit = kind === 'video' ? '段视频' : '张图';
  lg(`${spec.label}：出了 ${items.length} ${unit}，用时 ${Math.round(clk() - t0)} 秒，取原${WHAT[kind]}`);
  pg(PROGRESS_MAX, PROGRESS_MAX);
  const out = await f.download(items, kind);
  if (onConversation) onConversation({ url: String((await d.url()) || ''), continued: f.continued, note });
  return out;
}
