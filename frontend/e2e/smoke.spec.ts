import { expect, type Page, test } from '@playwright/test';
import { answer, STORYBOARD, STRUCTURE, SYNOPSIS, VISUAL } from './fixtures';

function nav(page: Page, name: string) {
  return page.getByRole('navigation', { name: '阶段导航' }).getByRole('link', { name, exact: true });
}

/** 到任务页，点开等待输入的那个任务，确认要的是哪一份输入，粘贴并提交。 */
async function answerWaitingJob(page: Page, key: RegExp, text: string) {
  await nav(page, '任务').click();
  await page.getByRole('row').filter({ hasText: '等待输入' }).first().click();
  await expect(page.getByTestId('input-key')).toHaveText(key);
  await page.getByLabel('模型输出').fill(text);
  await page.getByRole('button', { name: '提交结果' }).click();
}

function stage(page: Page, name: RegExp) {
  return page.getByRole('list', { name: '制作阶段' }).getByRole('button', { name });
}

test('新建项目 → 单集工作台：剧本 → 分镜 → 首帧阶段（manual LLM，不需要显卡）', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '新建项目' }).click();
  await page.getByLabel('项目标题').fill('冒烟');
  await page.getByRole('button', { name: '创建项目' }).click();
  await expect(page.getByRole('heading', { name: '冒烟' })).toBeVisible();

  await page.getByRole('button', { name: '新建剧集' }).click();
  await page.getByLabel('原文或梗概').fill(SYNOPSIS);
  await page.getByLabel('目标时长（秒）').fill('10');
  await page.getByRole('button', { name: '创建剧集' }).click();
  // 新的一集：打开工作台，停在下一步所在的剧本阶段
  await expect(stage(page, /剧本/)).toHaveAttribute('aria-current', 'step');
  await expect(page).toHaveURL(/\?stage=script$/);
  await expect(page.getByRole('navigation', { name: '面包屑' })).toContainText('冒烟');

  await page.getByRole('button', { name: '生成剧本', exact: true }).click();
  await expect(page.locator('.job-notice')).toContainText('生成剧本：');

  // 剧本任务先要结构，再要视觉设定：同一个任务连着两次等待输入
  await answerWaitingJob(page, /^script_structure$/, JSON.stringify(STRUCTURE));
  await expect(page.getByTestId('input-key')).toHaveText('script_visual');
  await page.getByLabel('模型输出').fill(JSON.stringify(VISUAL));
  await page.getByRole('button', { name: '提交结果' }).click();
  await expect(page.getByTestId('job-status')).toHaveText('完成');

  // 左侧导航的「当前集」回到工作台：还没有镜头，下一步还在剧本阶段
  await nav(page, '当前集').click();
  await expect(page.getByRole('heading', { name: '剧本段落（1）' })).toBeVisible();
  await expect(page.getByRole('heading', { name: '第 1 集 提词器', level: 2 })).toBeVisible();
  const next = page.getByRole('button', { name: '下一步：生成分镜' });
  await expect(next).toBeVisible();
  // 剧本跑完后角色在左栏：点了切到角色阶段、打开角色抽屉；关掉抽屉回到角色卡网格
  await page.getByRole('complementary', { name: '剧本大纲与角色' }).getByRole('button', { name: /^林夏/ }).click();
  await expect(page).toHaveURL(/\?stage=cast&char=\d+$/);
  const charDrawer = page.getByRole('dialog', { name: '角色 林夏' });
  await expect(charDrawer).toBeVisible();
  await charDrawer.getByRole('button', { name: '关闭' }).click();
  await expect(charDrawer).toBeHidden();
  await expect(page).toHaveURL(/\?stage=cast$/);
  await expect(page.getByRole('button', { name: /^打开 林夏/ })).toBeVisible();
  // 阶段条切回剧本，「下一步」就在本阶段：点它直接触发生成分镜
  await stage(page, /剧本/).click();
  await next.click();
  await expect(page.locator('.job-notice')).toContainText('生成分镜：');

  await answerWaitingJob(page, /^storyboard:\d+$/, JSON.stringify(STORYBOARD));
  await expect(page.getByTestId('job-status')).toHaveText('完成');

  // 有镜头了：剧本阶段是「重新生成分镜…」，对话框里有修改意见和影响预估；取消不提交
  await nav(page, '当前集').click();
  await stage(page, /剧本/).click();
  await page.getByRole('button', { name: '重新生成分镜…' }).click();
  const regen = page.getByRole('dialog', { name: '重新生成分镜' });
  await expect(regen).toContainText('范围：整集');
  await expect(regen).toContainText('删除 2 个镜头');
  await expect(regen.getByLabel('修改意见（可选）')).toBeVisible();
  await regen.getByRole('button', { name: '取消' }).click();
  await expect(regen).toBeHidden();

  await stage(page, /首帧/).click();
  await expect(page).toHaveURL(/\?stage=keyframes$/);
  await expect(page.getByTestId('shot-S01')).toBeVisible();
  await expect(page.getByTestId('shot-S02')).toBeVisible();
  await expect(page.getByTestId('shot-S01')).toContainText('1 句台词');
  await expect(page.getByTestId('shot-S01')).toContainText('未生成');
  // 镜头按段落分组；下一步是首帧确认门：先补缺少的首帧
  await expect(page.getByRole('region', { name: /^第 1 段 · / })).toBeVisible();
  await expect(page.getByRole('button', { name: /^下一步：生成缺少\/过期的首帧（2 张，/ })).toBeVisible();

  // 生产态深链接刷新仍然是这个页面（后端 SPA 回落）
  await page.reload();
  await expect(page.getByTestId('shot-S02')).toBeVisible();
});

test('ComfyUI 不通时点生成首帧：提交被拒，页面说明原因并给去设置的入口', async ({ page, playwright, baseURL }) => {
  // 数据走 API 准备到「分镜已有、首帧未生成」；项目默认的出图引擎跑在 ComfyUI 上，而 e2e 服务器把 ComfyUI 指到了死地址
  const api = await playwright.request.newContext({ baseURL });
  const slug = 'comfy-down';
  expect((await api.post('/api/projects', { data: { title: 'ComfyUI 断开', slug } })).ok()).toBeTruthy();
  const ep = await (await api.post(`/api/projects/${slug}/episodes`, { data: { number: 1, source_text: SYNOPSIS, target_seconds: 10 } })).json();
  const run = async (stage: string) => (await (await api.post(`/api/episodes/${ep.id}/run`, { data: { stage } })).json()).job_ids[0];
  expect((await answer(api, await run('script'), [STRUCTURE, VISUAL])).status).toBe('done');
  expect((await answer(api, await run('storyboard'), [STORYBOARD])).status).toBe('done');
  const jobsBefore = (await (await api.get('/api/jobs')).json()).length;

  // 旧的导演台地址重定向到工作台的首帧阶段
  await page.goto(`/projects/${slug}/episodes/${ep.id}/director`);
  await expect(page).toHaveURL(/\?stage=keyframes$/);
  await expect(page.getByTestId('shot-S01')).toContainText('未生成');
  await page.getByRole('button', { name: /^下一步：生成缺少\/过期的首帧/ }).click();

  // 提交前就被拒：就地提示写明原因，不排进队列
  await expect(page.locator('.job-notice')).toContainText('ComfyUI 连不上');
  await expect(page.locator('.job-notice')).toContainText('设置页');
  expect((await (await api.get('/api/jobs')).json()).length).toBe(jobsBefore);
  await expect(page.getByTestId('shot-S01')).toContainText('未生成');

  // 健康横幅给出根因和去设置的入口
  const banner = page.getByTestId('health-banner');
  await expect(banner).toContainText('ComfyUI 没连上，本机出图、出视频会失败');
  await banner.getByRole('link', { name: '去设置' }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole('heading', { name: '引擎' })).toBeVisible();
  await api.dispose();
});

test('重新生成分镜（带修改意见）：等待输入的提示词里带着修改意见和上一版镜头，跑完镜头换成新的', async ({ page, playwright, baseURL }) => {
  const api = await playwright.request.newContext({ baseURL });
  const slug = 'regen-note';
  expect((await api.post('/api/projects', { data: { title: '重新生成', slug } })).ok()).toBeTruthy();
  const ep = await (await api.post(`/api/projects/${slug}/episodes`, { data: { number: 1, source_text: SYNOPSIS, target_seconds: 10 } })).json();
  const run = async (stage: string) => (await (await api.post(`/api/episodes/${ep.id}/run`, { data: { stage } })).json()).job_ids[0];
  expect((await answer(api, await run('script'), [STRUCTURE, VISUAL])).status).toBe('done');
  expect((await answer(api, await run('storyboard'), [STORYBOARD])).status).toBe('done');

  await page.goto(`/projects/${slug}/episodes/${ep.id}?stage=script`);
  await page.getByRole('button', { name: '重新生成分镜…' }).click();
  const regen = page.getByRole('dialog', { name: '重新生成分镜' });
  await expect(regen).toContainText('删除 2 个镜头');
  const NOTE = '第二镜改成她猛地站起来';
  await regen.getByLabel('修改意见（可选）').fill(NOTE);
  await regen.getByRole('button', { name: '重新生成' }).click();
  await expect(regen).toBeHidden();
  await expect(page.locator('.job-notice')).toContainText('分镜');

  // 任务页：等待输入的那一份用户提示词里写着修改意见，并附上了上一版的镜头
  await nav(page, '任务').click();
  await page.getByRole('row').filter({ hasText: '等待输入' }).first().click();
  await expect(page.getByTestId('input-key')).toHaveText(/^storyboard:\d+$/);
  const user = page.getByLabel('用户提示词（user）');
  await expect(user).toHaveValue(new RegExp(`修改意见：${NOTE}`));
  await expect(user).toHaveValue(/上一版本段镜头[\s\S]*林夏照着提词器念稿/);

  const changed = { shots: [STORYBOARD.shots[0], { ...STORYBOARD.shots[1], action: '林夏猛地站起来' }] };
  await page.getByLabel('模型输出').fill(JSON.stringify(changed));
  await page.getByRole('button', { name: '提交结果' }).click();
  await expect(page.getByTestId('job-status')).toHaveText('完成');

  await page.goto(`/projects/${slug}/episodes/${ep.id}?stage=keyframes`);
  await expect(page.getByTestId('shot-S02')).toContainText('林夏猛地站起来');
  await api.dispose();
});
