import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { ScriptReview } from '../../api/types';
import { installFetch } from '../../test/fetch';
import { makeEpisode, makeShot } from '../../test/factories';
import { renderAt } from '../../test/render';
import { ScriptReviewPanel, reviewSummary } from './ScriptReviewPanel';
import { ScriptStage } from './ScriptStage';

const RULES: ScriptReview['rules'] = {
  total_seconds: 20,
  shot_count: 2,
  target_seconds: 20,
  findings: [
    { key: 'first_line', severity: 'warn', title: '第一句台词在第 9 秒（S02）', detail: '建议 5 秒内就有第一句台词', shots: ['S02'] },
    { key: 'long_shots', severity: 'warn', title: '1 个镜头超过 10 秒', detail: '拆成两镜', shots: ['S02'] },
    { key: 'ending', severity: 'ok', title: '最后一镜（S02）停在台词或强动作上', detail: '', shots: ['S02'] },
  ],
};
const REVIEW: ScriptReview['review'] = {
  hook: { score: 4, reason: 'S01 是空镜' },
  conflict: { score: 6, reason: '有冲突没反击' },
  ending: { score: 8, reason: '停在董事长进门' },
  pacing: { score: 5, reason: 'S02 太长' },
  summary: '开头要改',
  suggestions: [{ dimension: 'hook', ref: 'S01', problem: '空镜开场', rewrite: '经理：「你被开除了。」' }],
  created_at: '2026-10-12T08:00:00',
  job_id: 3,
  stale: false,
};
const AT = { route: '/projects/demo/episodes/4', path: '/projects/:slug/episodes/:episodeId' };
const writes = <T extends { method: string }>(calls: T[]) => calls.filter((c) => c.method !== 'GET');

describe('ScriptReviewPanel', () => {
  it('收起时一行摘要；展开就有规则检查，还没 AI 体检时点「开始体检」提交', async () => {
    let data: ScriptReview = { episode_id: 4, rules: RULES, review: null, job: null };
    const calls = installFetch({
      'GET /api/episodes/4/script-review': () => data,
      'POST /api/episodes/4/script-review': () => {
        data = { ...data, job: { id: 12, status: 'queued', error: null } };
        return { job_ids: [12] };
      },
    });
    renderAt(<ScriptReviewPanel episodeId={4} />, AT);
    const user = userEvent.setup();
    const head = await screen.findByRole('button', { name: /剧本体检/ });
    await waitFor(() => expect(head.textContent).toContain('规则检查 2 项要注意'));
    expect(head.getAttribute('aria-expanded')).toBe('false');
    await user.click(head);
    const rules = screen.getByRole('list', { name: '规则检查' });
    expect(within(rules).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      '注意第一句台词在第 9 秒（S02）：建议 5 秒内就有第一句台词',
      '注意1 个镜头超过 10 秒：拆成两镜',
      '通过最后一镜（S02）停在台词或强动作上',
    ]);
    expect(screen.getByText(/还没做过 AI 体检/)).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '开始体检' }));
    await waitFor(() => expect(writes(calls)).toHaveLength(1));
    expect(writes(calls)[0].url).toBe('/api/episodes/4/script-review');
    expect(await screen.findByText(/AI 体检排队中/)).toBeTruthy();
    expect((screen.getByRole('button', { name: '开始体检' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('有 AI 体检结果：四项打分、改写建议、分镜改过的提醒，按钮是「重新体检」', async () => {
    installFetch({
      'GET /api/episodes/4/script-review': { episode_id: 4, rules: RULES, review: { ...REVIEW, stale: true }, job: { id: 3, status: 'done', error: null } },
    });
    renderAt(<ScriptReviewPanel episodeId={4} />, AT);
    const user = userEvent.setup();
    const head = await screen.findByRole('button', { name: /剧本体检/ });
    await waitFor(() => expect(head.textContent).toContain('AI 体检平均 5.8 分（分镜改过了）'));
    await user.click(head);
    const scores = screen.getByRole('list', { name: 'AI 打分' });
    expect(within(scores).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      '4/10开场钩子（前 15 秒）：S01 是空镜',
      '6/10冲突与爽点密度：有冲突没反击',
      '8/10结尾卡点：停在董事长进门',
      '5/10节奏：S02 太长',
    ]);
    const sugs = screen.getByRole('list', { name: '改写建议' });
    expect(sugs.textContent).toContain('S01');
    expect(sugs.textContent).toContain('→ 经理：「你被开除了。」');
    expect(screen.getByText(/分镜或段落改过了/)).toBeTruthy();
    expect(screen.getByRole('button', { name: '重新体检' })).toBeTruthy();
  });

  it('最近一次体检失败时写明原因（之前的结果照样显示）', async () => {
    installFetch({
      'GET /api/episodes/4/script-review': { episode_id: 4, rules: RULES, review: REVIEW, job: { id: 9, status: 'failed', error: 'LLM 连不上' } },
    });
    renderAt(<ScriptReviewPanel episodeId={4} />, AT);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /剧本体检/ }));
    expect(await screen.findByText('上次体检失败：LLM 连不上')).toBeTruthy();
    expect(screen.getByRole('list', { name: 'AI 打分' })).toBeTruthy();
  });

  it('reviewSummary：都通过、体检中', () => {
    const ok = { episode_id: 1, rules: { ...RULES, findings: [RULES.findings[2]] }, review: null, job: { id: 1, status: 'running' as const, error: null } };
    expect(reviewSummary(ok)).toBe('规则检查都通过 · AI 体检中…');
  });

  it('剧本页：有镜头才显示剧本体检', async () => {
    const ep = makeEpisode({ id: 4, scene_blocks: [{ id: 21, order: 0, location_id: null, text: '直播间' }] });
    const routes = {
      'GET /api/projects/demo/locations': [],
      'GET /api/projects/demo/characters': [],
      'GET /api/projects/demo/style': null,
      'GET /api/episodes/4/script-review': { episode_id: 4, rules: RULES, review: null, job: null },
    };
    installFetch({ ...routes, 'GET /api/episodes/4': ep });
    const first = renderAt(<ScriptStage />, { ...AT, route: '/projects/demo/episodes/4?stage=script' });
    expect(await screen.findByRole('heading', { name: '剧本段落（1）' })).toBeTruthy();
    expect(screen.queryByTestId('script-review')).toBeNull();
    first.unmount();
    installFetch({ ...routes, 'GET /api/episodes/4': { ...ep, shots: [makeShot({ id: 1 })] } });
    renderAt(<ScriptStage />, { ...AT, route: '/projects/demo/episodes/4?stage=script' });
    expect(await screen.findByTestId('script-review')).toBeTruthy();
  });
});
