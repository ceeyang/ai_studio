import { describe, expect, it } from 'vitest';
import { VOICES, engineLabel, engineOptionText, fmtDuration, fmtShortTime, fmtTime, jobStatusLabel, jobTypeLabel } from './format';

/** 后端 HANDLERS.register 登记的全部任务类型（grep -rn "HANDLERS.register" backend）。新增类型时这里也要加。 */
const BACKEND_JOB_TYPES = [
  'script',
  'storyboard',
  'script.review',
  'image.keyframe',
  'video.shot',
  'export.episode',
  'image.portrait',
  'image.turnaround',
  'character.suggest',
  'character.recommend',
  'character.turnaround_look',
  'character.from_photo',
  'post.dub',
  'subtitles.translate',
  'dialogue.fit_khmer',
] as const;

describe('format', () => {
  it('任务类型和状态的中文名，没见过的类型显示「其他任务」', () => {
    expect(jobTypeLabel('video.shot')).toBe('视频');
    expect(jobTypeLabel('script')).toBe('剧本');
    expect(jobTypeLabel('image.keyframe')).toBe('首帧');
    expect(jobTypeLabel('upscale')).toBe('其他任务');
    expect(jobStatusLabel('waiting_input')).toBe('等待输入');
  });

  it('后端每一种任务类型都有中文名（不含英文 id）', () => {
    for (const t of BACKEND_JOB_TYPES) {
      const label = jobTypeLabel(t);
      expect(label, t).not.toBe('其他任务');
      expect(label, t).not.toBe(t);
      expect(/[一-龥]/.test(label), `${t} → ${label}`).toBe(true);
    }
  });

  it('引擎中文名：传 id 或引擎对象都行，没见过的原样返回', () => {
    expect(engineLabel('zimage')).toBe('Z-Image 快速');
    expect(engineLabel('h3_image')).toBe('H3 图像');
    expect(engineLabel('krea2')).toBe('Krea2 人像');
    expect(engineLabel('gpt_image')).toBe('ChatGPT 网页');
    expect(engineLabel('gemini_image')).toBe('Gemini 网页');
    expect(engineLabel('h3_video')).toBe('H3 视频（有声）');
    expect(engineLabel('wan22_i2v')).toBe('Wan2.2 图生视频（无声）');
    expect(engineLabel({ name: 'gpt_image', runtime: 'browser' })).toBe('ChatGPT 网页');
    expect(engineLabel('mystery')).toBe('mystery');
    expect(engineLabel({ name: 'rh_flux', runtime: 'runninghub', label: 'Flux 云端' })).toBe('Flux 云端（RunningHub 云端）');
    expect(engineLabel({ name: 'rh_x', runtime: 'runninghub' })).toBe('rh_x（RunningHub 云端）');
    expect(engineLabel({ name: 'site_x', runtime: 'browser' })).toBe('site_x（浏览器）');
  });

  it('常用音色表：有中文普通话和高棉语的音色，各带中文说明', () => {
    const ids = VOICES.map((v) => v.id);
    expect(ids).toContain('zh-CN-XiaoxiaoNeural');
    expect(ids).toContain('km-KH-PisethNeural');
    expect(ids).toContain('km-KH-SreymomNeural');
    expect(ids.filter((id) => id.startsWith('zh-CN-')).length).toBe(4);
    for (const v of VOICES) expect(v.label.length).toBeGreaterThan(0);
  });

  it('耗时：不到一分钟按秒，超过按分秒；没有时区的时间按 UTC', () => {
    expect(fmtDuration('2026-10-08T10:00:00Z', '2026-10-08T10:01:05Z')).toBe('1 分 5 秒');
    expect(fmtDuration('2026-10-08T10:00:00', '2026-10-08T10:00:30Z')).toBe('30 秒');
    expect(fmtDuration(null, null)).toBe('—');
    expect(fmtDuration('2026-10-08T10:00:00Z', null, Date.parse('2026-10-08T10:00:10Z'))).toBe('10 秒');
  });

  it('时间为空时显示占位符', () => {
    expect(fmtTime(null)).toBe('—');
    expect(fmtTime('2026-10-08T10:00:00Z')).not.toBe('—');
  });

  it('列表短时间：月/日 时:分，按本地时区', () => {
    expect(fmtShortTime(null)).toBe('—');
    const d = new Date(Date.UTC(2026, 9, 8, 10, 5, 30));
    const p2 = (n: number) => String(n).padStart(2, '0');
    expect(fmtShortTime('2026-10-08T10:05:30')).toBe(`${d.getMonth() + 1}/${d.getDate()} ${p2(d.getHours())}:${p2(d.getMinutes())}`);
  });
});

describe('engineOptionText', () => {
  it('不可用的引擎按运行方式写原因', () => {
    expect(engineOptionText({ name: 'h3_video', runtime: 'comfyui', ready: true })).toBe('H3 视频（有声）');
    expect(engineOptionText({ name: 'h3_video', runtime: 'comfyui', ready: false })).toBe('H3 视频（有声）（未配置：ComfyUI 没连上）');
    expect(engineOptionText({ name: 'x', label: 'X', runtime: 'api', ready: false })).toBe('X（先在 API 服务里填 Key）');
  });
});
