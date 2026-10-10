import { expect, type Page, test } from '@playwright/test';
import { STORYBOARD, STRUCTURE, SYNOPSIS, VISUAL } from './fixtures';

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

test('新建项目 → 剧本 → 分镜 → 导演台（manual LLM，不需要显卡）', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: '新建项目' }).click();
  await page.getByLabel('项目标题').fill('冒烟');
  await page.getByRole('button', { name: '创建项目' }).click();
  await expect(page.getByRole('heading', { name: '冒烟' })).toBeVisible();

  await page.getByRole('button', { name: '新建剧集' }).click();
  await page.getByLabel('原文或梗概').fill(SYNOPSIS);
  await page.getByLabel('目标时长（秒）').fill('10');
  await page.getByRole('button', { name: '创建剧集' }).click();
  await expect(page.getByRole('heading', { name: '剧本', exact: true, level: 2 })).toBeVisible();
  await expect(page.getByRole('navigation', { name: '面包屑' })).toContainText('冒烟');

  await page.getByRole('button', { name: '运行剧本', exact: true }).click();
  await expect(page.locator('.job-notice')).toContainText('运行剧本：');

  // 剧本任务先要结构，再要视觉设定：同一个任务连着两次等待输入
  await answerWaitingJob(page, /^script_structure$/, JSON.stringify(STRUCTURE));
  await expect(page.getByTestId('input-key')).toHaveText('script_visual');
  await page.getByLabel('模型输出').fill(JSON.stringify(VISUAL));
  await page.getByRole('button', { name: '提交结果' }).click();
  await expect(page.getByTestId('job-status')).toHaveText('完成');

  await nav(page, '剧本').click();
  await expect(page.getByRole('heading', { name: '剧本段落（1）' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: '面包屑' })).toContainText('第 1 集 提词器');
  // 剧本有了、角色还没定妆照：页头的下一步指向角色页
  await expect(page.getByRole('link', { name: /^下一步：抽定妆照/ })).toBeVisible();
  // 剧本跑完后角色在角色页：右上「本集角色」条进去
  await page.getByRole('link', { name: /本集角色/ }).click();
  await expect(page.getByRole('heading', { name: '林夏', level: 4 })).toBeVisible();
  await nav(page, '剧本').click();
  await page.getByRole('button', { name: '运行分镜', exact: true }).click();
  await expect(page.locator('.job-notice')).toContainText('运行分镜：');

  await answerWaitingJob(page, /^storyboard:\d+$/, JSON.stringify(STORYBOARD));
  await expect(page.getByTestId('job-status')).toHaveText('完成');

  await nav(page, '导演台').click();
  await expect(page.getByTestId('shot-S01')).toBeVisible();
  await expect(page.getByTestId('shot-S02')).toBeVisible();
  await expect(page.getByTestId('shot-S01')).toContainText('林夏：欢迎回到直播间');
  await expect(page.getByTestId('shot-S01')).toContainText('未生成');

  // 生产态深链接刷新仍然是这个页面（后端 SPA 回落）
  await page.reload();
  await expect(page.getByTestId('shot-S02')).toBeVisible();
});
