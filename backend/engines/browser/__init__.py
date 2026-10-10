"""网页图像引擎：在一个已经登录 ChatGPT / Gemini 的真实浏览器里出图（不用 API key）。

browser.transport=extension（默认）：出图在用户平时用的浏览器里，由仓库下的 browser-extension 完成。
- extension.py：扩展桥（WebSocket 协议、发任务、收分块原图、断开/取消/超时）

browser.transport=cdp（备用）：studio 自己带调试端口启动独立配置目录的浏览器，Playwright 连上去。
- session.py：找 Chrome/Edge、带调试端口启动独立配置目录、CDP 连接、读登录 cookie
- driver.py：页面操作的薄封装（PageDriver），Playwright 实现；测试用假的
- flow.py：通用出图流程（新对话 → 传参考图 → 输提示词 → 提交 → 等图 → 下原图），出错给中文原因
- sites/：每个网站的选择器和文案匹配，网站改版时只改这里
- runner.py：任务执行入口（每个网站一把锁、一个标签页，按 transport 分派），落盘、按画幅裁切
- engines.py：gpt_image / gemini_image 两个图像引擎（把请求拼成网页出图请求）
"""
