## 改了什么

<!-- 一两句话说用户能感知到的变化；关联的 issue 写 Closes #123 -->

## 为什么

## 怎么验证的

- [ ] `uv run pytest backend/tests`
- [ ] `cd frontend && npm run typecheck && npx vitest run`
- [ ] `cd frontend && npm run build`
- [ ] 改了浏览器扩展：跑过 `uv run --no-sync python browser-extension/dev/smoke.py`，或说明在哪个网站上实测过
- [ ] 改了数据库模型：加了 Alembic 迁移（`backend/migrations/versions/`），并接在当前唯一的头后面
- [ ] 修 bug：加了能复现的测试

## 其他

- [ ] 没有提交 API Key、cookie、本机绝对路径、个人或客户信息
- [ ] 提交信息照仓库的写法（`feat:` / `fix(范围):` + 中文说明，`——` 后写细节）

<!-- 有界面变化请附截图（遮掉个人信息） -->
