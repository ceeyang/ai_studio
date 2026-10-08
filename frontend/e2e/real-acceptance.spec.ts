// 真实环境验收：对着已经在跑的 studio-server（生产态）走完 梗概 → 成片。只在设了 STUDIO_REAL_URL 时运行：
//   $env:STUDIO_REAL_URL='http://127.0.0.1:8700'; npx playwright test
import { expect, type Page, test } from '@playwright/test';

const SYNOPSIS =
  '深夜的直播间，女主播林夏照着提词器念稿，突然发现屏幕上的词被人改成了她的秘密。她停顿了一秒，抬头看向导播间。';

test.setTimeout(60 * 60 * 1000);

async function waitQueueIdle(page: Page, slug: string) {
  await page.waitForTimeout(2000);
  await expect
    .poll(
      async () => {
        const q = await (await page.request.get('/api/queue')).json();
        return q.counts.queued + q.counts.running;
      },
      { timeout: 40 * 60 * 1000, intervals: [5000] },
    )
    .toBe(0);
  const failed = await (await page.request.get(`/api/jobs?status=failed&project=${encodeURIComponent(slug)}`)).json();
  expect(failed, JSON.stringify(failed.map((j: { id: number; error: string }) => [j.id, j.error]))).toHaveLength(0);
}

test('真实环境：梗概 → 带 H3 原生声音的成片', async ({ page }) => {
  const slug = `网页验收${Date.now() % 100000}`;
  await page.goto('/');
  await page.getByLabel('项目标题').fill(slug);
  await page.getByLabel('画幅').selectOption('9:16');
  await page.getByLabel('图像引擎').selectOption('zimage');
  await page.getByLabel('视频引擎').selectOption('h3_video');
  await page.getByRole('button', { name: '新建项目' }).click();
  await page.getByLabel('原文或梗概').fill(SYNOPSIS);
  await page.getByLabel('目标时长（秒）').fill('15');
  await page.getByRole('button', { name: '新建剧集' }).click();

  await page.getByRole('button', { name: '运行剧本' }).click();
  await waitQueueIdle(page, slug);
  await page.reload();
  await page.getByRole('button', { name: '运行分镜' }).click();
  await waitQueueIdle(page, slug);

  await page.getByRole('navigation', { name: '阶段导航' }).getByRole('link', { name: '导演台', exact: true }).click();
  await expect(page.getByTestId('shot-S01')).toBeVisible();
  await page.getByRole('button', { name: '生成首帧（全部）' }).click();
  await waitQueueIdle(page, slug);
  await page.getByRole('button', { name: '生成视频（全部）' }).click();
  await waitQueueIdle(page, slug);
  await page.getByRole('button', { name: '合成导出' }).click();
  await waitQueueIdle(page, slug);

  await page.reload();
  const link = page.getByTestId('export-link').first();
  await expect(link).toBeVisible();
  console.log(`项目 ${slug}，成片：${await link.getAttribute('href')}`);
});
