"""网页图像引擎：驱动一个已经登录 ChatGPT / Gemini 的真实浏览器出图（不用 API key）。

- session.py：找 Chrome/Edge、带调试端口启动独立配置目录、CDP 连接、读登录 cookie
- driver.py：页面操作的薄封装（PageDriver），Playwright 实现；测试用假的
- flow.py：通用出图流程（新对话 → 传参考图 → 输提示词 → 提交 → 等图 → 下原图），出错给中文原因
- sites/：每个网站的选择器和文案匹配，网站改版时只改这里
- runner.py：任务执行入口（每个网站一把锁、一个标签页），落盘、按画幅裁切
- engines.py：gpt_image / gemini_image 两个图像引擎（把请求拼成网页出图请求）
"""
