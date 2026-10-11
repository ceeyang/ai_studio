import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// 定妆照改成 3:4 出图：卡片框也要 3:4，图片用 contain（以前的 9:16 定妆照整张放进去，不裁掉头）
describe('定妆照卡片样式', () => {
  const css = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8');
  it('框是 3:4，图片 contain', () => {
    expect(css).toMatch(/\.pcard, \.pcard-img, \.pcard-empty \{[^}]*aspect-ratio: 3 \/ 4/);
    expect(css).toMatch(/\.pcard img \{[^}]*object-fit: contain/);
  });
});
