import { describe, expect, it } from 'vitest';
import { makeJob } from '../../test/factories';
import { dateGroup, errorBrief, fmtSeconds, groupByDate, matchesSearch, relTime, typeGroupOf } from './jobList';

const NOW = new Date(2026, 9, 11, 15, 0, 0).getTime(); // 本地时间 2026-10-11 15:00
const local = (d: number, h: number, m = 0) => new Date(2026, 9, d, h, m).toISOString();

describe('jobList', () => {
  it('相对时间：刚刚 / N 分钟前 / N 小时前 / 昨天 HH:mm / M/D HH:mm', () => {
    expect(relTime(new Date(NOW - 20_000).toISOString(), NOW)).toBe('刚刚');
    expect(relTime(local(11, 14, 59), NOW)).toBe('1 分钟前');
    expect(relTime(local(11, 14, 57), NOW)).toBe('3 分钟前');
    expect(relTime(local(11, 12, 0), NOW)).toBe('3 小时前');
    expect(relTime(local(10, 20, 29), NOW)).toBe('昨天 20:29');
    expect(relTime(local(3, 8, 5), NOW)).toBe('10/3 08:05');
    expect(relTime(null, NOW)).toBe('—');
  });

  it('日期分组按本地日期：今天 / 昨天 / 更早；组内保持顺序', () => {
    expect(dateGroup(local(11, 0, 1), NOW)).toBe('today');
    expect(dateGroup(local(10, 23, 59), NOW)).toBe('yesterday');
    expect(dateGroup(local(9, 23, 59), NOW)).toBe('earlier');
    const jobs = [
      makeJob({ id: 4, created_at: local(11, 9) }),
      makeJob({ id: 3, created_at: local(11, 8) }),
      makeJob({ id: 2, created_at: local(10, 8) }),
      makeJob({ id: 1, created_at: local(1, 8) }),
    ];
    expect(groupByDate(jobs, NOW).map((g) => [g.group, g.jobs.map((j) => j.id)])).toEqual([
      ['today', [4, 3]],
      ['yesterday', [2]],
      ['earlier', [1]],
    ]);
  });

  it('失败原因摘要：第一行非空内容，超长截断', () => {
    expect(errorBrief('\n  显存不足  \n第二行')).toBe('显存不足');
    const s = errorBrief('x'.repeat(200), 20);
    expect(s).toHaveLength(20);
    expect(s.endsWith('…')).toBe(true);
    expect(errorBrief(null)).toBe('');
  });

  it('搜索：#编号精确匹配，镜头号 / 项目 / 类型中文名都能搜，多个词都要命中', () => {
    const j = makeJob({
      id: 293,
      type: 'video.shot',
      context: { label: '第 1 集 S01', url: null, project_slug: 'demo', episode_id: 1, shot_id: 1, character_id: null },
    });
    expect(matchesSearch(j, '#293')).toBe(true);
    expect(matchesSearch(j, '29')).toBe(false);
    expect(matchesSearch(j, 's01')).toBe(true);
    expect(matchesSearch(j, '视频 demo')).toBe(true);
    expect(matchesSearch(j, '视频 other')).toBe(false);
    expect(matchesSearch(j, '  ')).toBe(true);
  });

  it('类型分组和秒数格式', () => {
    expect(typeGroupOf('image.continue_frame')).toBe('keyframe');
    expect(typeGroupOf('weird')).toBe('other');
    expect(fmtSeconds(42)).toBe('42 秒');
    expect(fmtSeconds(125)).toBe('2 分 5 秒');
    expect(fmtSeconds(3725)).toBe('1 小时 2 分');
  });
});
