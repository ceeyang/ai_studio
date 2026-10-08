import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFetch } from '../test/fetch';
import { makeEpisode } from '../test/factories';
import { renderAt } from '../test/render';
import { ScriptPage } from './ScriptPage';

const EP = makeEpisode({
  id: 4,
  title: '提词器',
  scene_blocks: [{ id: 21, order: 0, location_id: 3, text: '直播间里，林夏念稿。' }],
});
const ROUTES = {
  'GET /api/episodes/4': EP,
  'GET /api/projects/demo/locations': [
    { id: 3, project_id: 1, name: '直播间', time_of_day: '夜', atmosphere: '压抑', visual_prompt: 'a dim studio' },
  ],
  'GET /api/projects/demo/characters': [
    { id: 8, project_id: 1, name: '林夏', description: '女主播', visual_prompt: 'young woman', core_features: '', voice: null },
  ],
  'GET /api/projects/demo/style': null,
};
const AT = { route: '/projects/demo/episodes/4/script', path: '/projects/:slug/episodes/:episodeId/script' };

describe('ScriptPage', () => {
  it('运行分镜：提交 storyboard 任务并给出任务链接', async () => {
    const calls = installFetch({ ...ROUTES, 'POST /api/episodes/4/run': { job_ids: [9] } });
    renderAt(<ScriptPage />, AT);
    const user = userEvent.setup();
    expect(await screen.findByRole('heading', { name: /剧本 · 第 1 集/ })).toBeTruthy();
    expect(screen.getByRole('heading', { name: '剧本段落（1）' })).toBeTruthy();
    await user.click(screen.getByRole('button', { name: '运行分镜' }));
    expect((await screen.findByRole('status')).textContent).toContain('已提交任务 #9');
    expect(screen.getByRole('link', { name: '查看任务' }).getAttribute('href')).toBe('/jobs?job=9');
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ stage: 'storyboard', force: false });
  });

  it('保存段落：提交文字和场景', async () => {
    const calls = installFetch({
      ...ROUTES,
      'PATCH /api/scene-blocks/21': { id: 21, order: 0, location_id: null, text: '新段落' },
    });
    renderAt(<ScriptPage />, AT);
    const user = userEvent.setup();
    const block = await screen.findByTestId('block-21');
    await within(block).findByRole('option', { name: '直播间' });
    const ta = within(block).getByLabelText('段落内容');
    await user.clear(ta);
    await user.type(ta, '新段落');
    await user.selectOptions(within(block).getByLabelText('场景'), '');
    await user.click(within(block).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(calls.find((c) => c.method === 'PATCH')).toMatchObject({
      url: '/api/scene-blocks/21',
      body: { text: '新段落', location_id: null },
    });
  });

  it('还没有段落时不能运行分镜；角色卡可编辑', async () => {
    const calls = installFetch({
      ...ROUTES,
      'GET /api/episodes/4': makeEpisode({ id: 4, scene_blocks: [] }),
      'PATCH /api/characters/8': { id: 8, project_id: 1, name: '林夏', description: '新主播', visual_prompt: 'young woman', core_features: '', voice: null },
    });
    renderAt(<ScriptPage />, AT);
    const user = userEvent.setup();
    expect(((await screen.findByRole('button', { name: '运行分镜' })) as HTMLButtonElement).disabled).toBe(true);
    const card = await screen.findByTestId('character-8');
    const desc = within(card).getByLabelText('人物描述');
    await user.clear(desc);
    await user.type(desc, '新主播');
    await user.click(within(card).getByRole('button', { name: '保存' }));
    await waitFor(() => expect(calls.some((c) => c.method === 'PATCH')).toBe(true));
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ description: '新主播' });
  });
});
