# AI Studio

本机 AI 漫剧工作台（私有）。设计文档：`docs/specs/2026-10-07-ai-studio-design.md`。

## 启动

    uv sync
    Copy-Item config.example.yaml config.yaml   # 按本机情况改
    uv run studio-server                        # http://127.0.0.1:8700

## 命令行

    uv run studio doctor
    uv run studio project create 提词员
    uv run studio episode import 提词员 --file 故事构思/提词员.md --seconds 90
    uv run studio run all 提词员 -e 1
    uv run studio jobs --watch
    uv run studio episode show 提词员 1

所有命令加 `--json` 输出 JSON。完整命令见 `uv run studio --help`。

## 测试

    uv run pytest
