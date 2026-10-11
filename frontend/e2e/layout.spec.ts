// 布局守卫：每个页面在 1024 / 1280 / 1580 / 1920 / 3412 宽下都不能横向溢出，主要面板里的内容也不能被挤出面板。
// 数据走 API + manual LLM 准备（剧本、分镜、造型方案、三视图全身描述、一个带长报错的失败任务），不需要显卡。
import { expect, type Page, test } from '@playwright/test';
import { answer, STORYBOARD, STRUCTURE, SYNOPSIS, VISUAL, waitJob } from './fixtures';

const SLUG = 'layout-check';
const WIDTHS = [1024, 1280, 1580, 1920, 3412];
// 1×1 透明 PNG
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
  'base64',
);
const LOOK = Array.from({ length: 70 }, (_, i) => (i % 7 === 0 ? 'navy-cotton-twill-workjacket' : 'detail')).join(' ');

let episodeId = 0;
let charId = 0;
let failedJob = 0;

test.beforeAll(async ({ playwright, baseURL }) => {
  const api = await playwright.request.newContext({ baseURL });
  expect((await api.post('/api/projects', { data: { title: '布局检查：一个标题很长很长的项目，用来看会不会把页面撑宽', slug: SLUG, image_engine: 'h3_image' } })).ok()).toBeTruthy();
  const ep = await (await api.post(`/api/projects/${SLUG}/episodes`, { data: { number: 1, source_text: SYNOPSIS.repeat(6), target_seconds: 10 } })).json();
  episodeId = ep.id;
  const run = async (stage: string) => (await (await api.post(`/api/episodes/${episodeId}/run`, { data: { stage } })).json()).job_ids[0];
  await answer(api, await run('script'), [STRUCTURE, VISUAL]);
  await answer(api, await run('storyboard'), [STORYBOARD]);

  const [ch] = await (await api.get(`/api/projects/${SLUG}/characters`)).json();
  charId = ch.id;
  await api.patch(`/api/characters/${ch.id}`, { data: { description: '二十多岁的女主播，'.repeat(12), visual_prompt: LOOK } });
  for (let i = 0; i < 6; i++) {
    await api.post(`/api/characters/${ch.id}/portrait`, { multipart: { file: { name: `p${i}.png`, mimeType: 'image/png', buffer: PNG } } });
  }
  const option = (label: string) => ({ label, visual_prompt: LOOK, core_features: '灰色开衫、齐肩黑发、银色耳钉、一个很长的标志性细节描述' });
  const sug = (await (await api.post(`/api/characters/${ch.id}/suggest`, { data: { count: 3 } })).json()).job_ids[0];
  await answer(api, sug, [{ options: [option('知性主播款'), option('清冷夜班款'), option('邻家学生款')] }]);
  const look = (await (await api.post(`/api/characters/${ch.id}/turnaround/look`, { data: {} })).json()).job_ids[0];
  await answer(api, look, [{ description: LOOK.split(' ').slice(0, 60).join(' ') }]);
  // 没有 ComfyUI：设定图会失败，任务页里留一条带长报错的失败任务
  failedJob = (await (await api.post(`/api/characters/${ch.id}/turnaround/generate`, { data: { sheets: ['face'] } })).json()).job_ids[0];
  await waitJob(api, failedJob, ['failed']);
  await api.dispose();
});

/** 页面不横向滚动；主要面板既不超出视口，里面的内容也不超出面板。 */
async function expectNoOverflow(page: Page, label: string) {
  const r = await page.evaluate(() => {
    const iw = window.innerWidth;
    const bad: string[] = [];
    const sel = '.content, .panel, .toolbar, .stage-bar, .ws-rail, .ws-main, .shot-grid-wrap, .shot-group, .shot-card, .drawer, .cast-card, .cast-settings, .turn-sheet, .turn-cell, .turn-look, .option-card, .project-card, .episode-card, .bible, .modal';
    for (const el of Array.from(document.querySelectorAll<HTMLElement>(sel))) {
      const rc = el.getBoundingClientRect();
      if (rc.width === 0) continue;
      const name = `${el.tagName.toLowerCase()}.${el.className.toString().trim().replace(/\s+/g, '.')}`;
      if (rc.right > iw + 1) bad.push(`${name} 右边 ${Math.round(rc.right)} > 视口 ${iw}`);
      if (el.scrollWidth > el.clientWidth + 1) bad.push(`${name} 内容 ${el.scrollWidth} > 宽 ${el.clientWidth}`);
    }
    return { sw: document.documentElement.scrollWidth, iw, bad };
  });
  expect(r.sw, `${label}：页面横向溢出`).toBeLessThanOrEqual(r.iw);
  expect(r.bad, `${label}：面板溢出`).toEqual([]);
}

for (const width of WIDTHS) {
  test(`${width} 宽：各页面不溢出`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    const ep = `/projects/${SLUG}/episodes/${episodeId}`;

    await page.goto('/');
    await expect(page.getByRole('heading', { name: '项目中心' })).toBeVisible();
    await expectNoOverflow(page, `${width} 项目中心`);

    await page.goto(`/projects/${SLUG}`);
    await expect(page.getByRole('heading', { name: /布局检查/ })).toBeVisible();
    await expectNoOverflow(page, `${width} 项目详情`);

    await page.goto(`${ep}?stage=script`);
    await expect(page.getByRole('heading', { name: /剧本段落/ })).toBeVisible();
    await expect(page.getByRole('complementary', { name: '剧本大纲与角色' })).toBeVisible();
    await expectNoOverflow(page, `${width} 剧本`);

    await page.goto(`${ep}?stage=cast`);
    await expect(page.getByTestId(`character-${charId}`)).toBeVisible();
    // 设定默认收起：里面的表单真的看不见（不只是 hidden 属性）
    await expect(page.getByLabel(/风格锚/)).toBeHidden();
    await page.getByRole('button', { name: /^设定/ }).click();
    await expect(page.getByLabel(/风格锚/)).toBeVisible();
    await expectNoOverflow(page, `${width} 角色`);
    // 角色抽屉：造型（方案卡）、三视图两个页签
    await page.goto(`${ep}?stage=cast&char=${charId}`);
    const drawer = page.getByRole('dialog', { name: /^角色 / });
    await expect(drawer.getByTestId('option-2')).toBeVisible();
    await drawer.evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
    await expectNoOverflow(page, `${width} 角色抽屉 造型`);
    // 页签一次只显示一个：造型页签里看不到三视图，切过去后看不到定妆照条
    await expect(drawer.getByRole('region', { name: /的三视图/ })).toBeHidden();
    await drawer.getByRole('tab', { name: '三视图' }).click();
    await expect(drawer.getByRole('region', { name: /的三视图/ })).toBeVisible();
    await expect(drawer.getByTestId('option-2')).toBeHidden();
    await expectNoOverflow(page, `${width} 角色抽屉 三视图`);

    await page.goto(`${ep}?stage=keyframes`);
    await expect(page.getByTestId('shot-S02')).toBeVisible();
    await expectNoOverflow(page, `${width} 首帧`);
    await page.getByRole('button', { name: '打开 S01' }).click();
    await expect(page.locator('.drawer')).toBeVisible();
    // 抽屉滑入动画（translateX 40px）中途量会多出 40px：等动画放完
    await page.locator('.drawer').evaluate((el) => Promise.all(el.getAnimations().map((a) => a.finished)));
    await expectNoOverflow(page, `${width} 镜头抽屉`);

    await page.goto(`${ep}?stage=export`);
    await expect(page.getByTestId('readiness')).toBeVisible();
    await expectNoOverflow(page, `${width} 导出`);

    await page.goto(`/jobs?job=${failedJob}`);
    await expect(page.getByTestId('job-status')).toHaveText('失败');
    // 1280 以下详情是从右侧滑入的抽屉：同样等动画放完再量
    await page.locator('.jd-wrap').evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)));
    await expectNoOverflow(page, `${width} 任务`);

    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: '引擎' })).toBeVisible();
    await expectNoOverflow(page, `${width} 设置`);
  });
}
