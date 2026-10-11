import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { installFetch } from '../../test/fetch';
import { renderAt } from '../../test/render';
import { FinishAllButton } from './AutopilotBar';

describe('一键出完剩下的', () => {
  it('还没有分镜时不显示', () => {
    installFetch({});
    renderAt(<FinishAllButton episodeId={4} hasShots={false} />);
    expect(screen.queryByRole('button', { name: '一键出完剩下的' })).toBeNull();
  });

  it('确认后排好任务，说排了几个、有几条提醒', async () => {
    const calls = installFetch({
      'POST /api/episodes/4/autopilot': { job_ids: [1, 2, 3], skipped: ['老人 还没有外貌提示词'], warnings: [] },
    });
    const user = userEvent.setup();
    renderAt(<FinishAllButton episodeId={4} hasShots />);
    await user.click(screen.getByRole('button', { name: '一键出完剩下的' }));
    await user.click(await screen.findByRole('button', { name: '开始' }));
    expect(await screen.findByText(/已排 3 个任务/)).toBeTruthy();
    expect(screen.getByText('（1 条提醒）')).toBeTruthy();
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(1);
  });

  it('取消确认就不提交', async () => {
    const calls = installFetch({ 'POST /api/episodes/4/autopilot': { job_ids: [], skipped: [], warnings: [] } });
    const user = userEvent.setup();
    renderAt(<FinishAllButton episodeId={4} hasShots />);
    await user.click(screen.getByRole('button', { name: '一键出完剩下的' }));
    await user.click(await screen.findByRole('button', { name: '取消' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: '开始' })).toBeNull());
    expect(calls.filter((c) => c.method === 'POST')).toHaveLength(0);
  });
});
