"""studio 命令行：调用本机工作台 HTTP API 的薄客户端。加 --json 输出机器可读结果。"""
from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

import httpx
import typer

DEFAULT_URL = "http://127.0.0.1:8700"

app = typer.Typer(help="AI Studio 命令行（调用本机工作台的 HTTP API）", no_args_is_help=True,
                  add_completion=False)
project_app = typer.Typer(help="项目", no_args_is_help=True)
episode_app = typer.Typer(help="剧集", no_args_is_help=True)
job_app = typer.Typer(help="单个任务", no_args_is_help=True)
candidate_app = typer.Typer(help="候选", no_args_is_help=True)
queue_app = typer.Typer(help="队列", no_args_is_help=True)
app.add_typer(project_app, name="project")
app.add_typer(episode_app, name="episode")
app.add_typer(job_app, name="job")
app.add_typer(candidate_app, name="candidate")
app.add_typer(queue_app, name="queue")

STATE = {"json": False}
ICON = {"ok": "√", "warn": "!", "fail": "×"}


def _base_url() -> str:
    return os.environ.get("STUDIO_URL", DEFAULT_URL)


def _client() -> httpx.Client:
    return httpx.Client(base_url=_base_url(), timeout=120, trust_env=False)


def _call(method: str, path: str, **kw) -> Any:
    try:
        r = _client().request(method, path, **kw)
    except httpx.TimeoutException:
        typer.echo(f"工作台响应超时（{_base_url()} {method} {path}），服务可能正忙，稍后用 "
                   "studio jobs 查看", err=True)
        raise typer.Exit(2)
    except httpx.TransportError:
        typer.echo(f"连不上工作台服务（{_base_url()}），先运行：uv run studio-server", err=True)
        raise typer.Exit(2)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text
        typer.echo(f"错误（HTTP {r.status_code}）：{detail}", err=True)
        raise typer.Exit(1)
    return r.json() if r.content else None


def _emit(data: Any, human: Callable[[Any], None]) -> None:
    if STATE["json"]:
        typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        human(data)


@app.callback()
def _root(as_json: bool = typer.Option(False, "--json", help="输出 JSON，便于脚本和 Claude 解析")):
    STATE["json"] = as_json


# ------------------------------------------------------------ 项目
def _print_projects(ps: list[dict]) -> None:
    if not ps:
        typer.echo("（还没有项目）")
    for p in ps:
        typer.echo(f"{p['slug']}\t{p['title']}\t{p['aspect']}\t{p['image_engine']} / {p['video_engine']}")


@project_app.command("list")
def project_list():
    _emit(_call("GET", "/api/projects"), _print_projects)


@project_app.command("create")
def project_create(title: str,
                   slug: Optional[str] = typer.Option(None, help="目录名，默认由标题生成"),
                   aspect: str = typer.Option("9:16", help="9:16 或 16:9"),
                   image_engine: str = typer.Option("zimage", "--image-engine"),
                   video_engine: str = typer.Option("h3_video", "--video-engine")):
    body = {"title": title, "aspect": aspect, "image_engine": image_engine,
            "video_engine": video_engine}
    if slug:
        body["slug"] = slug
    _emit(_call("POST", "/api/projects", json=body), lambda p: typer.echo(f"已创建项目 {p['slug']}"))


# ------------------------------------------------------------ 剧集
@episode_app.command("import")
def episode_import(project: str,
                   file: Path = typer.Option(..., "--file", "-f", exists=True, dir_okay=False,
                                             help="梗概或小说文本（UTF-8）"),
                   title: str = typer.Option("", help="本集标题，留空由剧本阶段生成"),
                   seconds: float = typer.Option(60.0, "--seconds", help="目标时长（秒）"),
                   number: Optional[int] = typer.Option(None, help="集数，默认接在最后")):
    text = file.read_text(encoding="utf-8")
    body: dict = {"title": title, "source_text": text, "target_seconds": seconds}
    if number:
        body["number"] = number
    _emit(_call("POST", f"/api/projects/{project}/episodes", json=body),
          lambda e: typer.echo(f"已导入第 {e['number']} 集（{len(text)} 字，目标 {seconds:.0f} 秒）"))


def _mark(shot: dict, kind: str) -> str:
    sl = next((x for x in shot.get("slots", []) if x["kind"] == kind), None)
    if not sl or not sl["selected_candidate_id"]:
        return "·"
    return "!" if sl["stale"] else "√"


def _print_episode(ep: dict) -> None:
    typer.echo(f"第 {ep['number']} 集 {ep['title']}  状态 {ep['status']}  "
               f"段落 {len(ep['scene_blocks'])}  镜头 {len(ep['shots'])}")
    typer.echo("编号   时长   首帧 视频  动作")
    for sh in ep["shots"]:
        typer.echo(f"{sh['code']:<6}{sh['duration']:>5.1f}s   {_mark(sh, 'first_frame')}    "
                   f"{_mark(sh, 'video')}    {sh['action'][:40]}")
    typer.echo("（√ 已选定  ! 已过期  · 还没有）")


@episode_app.command("show")
def episode_show(project: str, number: int):
    _emit(_call("GET", f"/api/projects/{project}/episodes/by-number/{number}"), _print_episode)


# ------------------------------------------------------------ 运行
@app.command("run")
def run(stage: str = typer.Argument(..., help="script / storyboard / keyframes / videos / export / all"),
        project: str = typer.Argument(...),
        episode: int = typer.Option(1, "--episode", "-e", help="集数"),
        shots: Optional[str] = typer.Option(None, help="只跑这些镜头，逗号分隔，如 S01,S03"),
        candidates: int = typer.Option(1, help="每个镜头出几张首帧候选（1–4）"),
        force: bool = typer.Option(False, help="输入没变也重新生成")):
    ep = _call("GET", f"/api/projects/{project}/episodes/by-number/{episode}")
    body: dict = {"stage": stage, "candidates": candidates, "force": force}
    if shots:
        body["shots"] = [x.strip() for x in shots.split(",") if x.strip()]
    _emit(_call("POST", f"/api/episodes/{ep['id']}/run", json=body),
          lambda r: typer.echo(f"已提交 {len(r['job_ids'])} 个任务：{', '.join(map(str, r['job_ids']))}"))


# ------------------------------------------------------------ 任务
def _print_jobs(jobs: list[dict]) -> None:
    if not jobs:
        typer.echo("（没有任务）")
    for j in jobs:
        err = f"  {j['error'].splitlines()[0][:60]}" if j.get("error") else ""
        typer.echo(f"#{j['id']:<5} {j['status']:<14}{j['type']:<16}{j.get('model_family') or '':<8}{err}")


@app.command("jobs")
def jobs(status: Optional[str] = typer.Option(None, help="逗号分隔，如 queued,running"),
         project: Optional[str] = typer.Option(None, help="项目 slug"),
         limit: int = typer.Option(30),
         watch: bool = typer.Option(False, help="每 2 秒刷新，直到队列空了")):
    params: dict = {"limit": limit}
    if status:
        params["status"] = status
    if project:
        params["project"] = project
    while True:
        _emit(_call("GET", "/api/jobs", params=params), _print_jobs)
        if not watch:
            return
        counts = _call("GET", "/api/queue")["counts"]
        if counts["queued"] + counts["running"] == 0:
            if counts.get("waiting_input"):
                typer.echo("有任务在等待人工输入，用 studio job show <id> 查看")
            else:
                typer.echo("队列已空")
            return
        time.sleep(2)
        typer.echo("")


def _print_job(j: dict) -> None:
    typer.echo(f"#{j['id']} {j['type']}  {j['status']}  族 {j.get('model_family') or '-'}")
    if j.get("error"):
        typer.echo(f"错误：{j['error']}")
    req = (j.get("result") or {}).get("input_request")
    if j["status"] == "waiting_input" and req:
        typer.echo(f"等待人工输入（key={req.get('key')}）。把下面的提示词交给任意模型，"
                   f"结果存成文件后运行：studio job input {j['id']} --file <文件>")
        typer.echo("---- system ----\n" + req.get("system", ""))
        typer.echo("---- user ----\n" + req.get("user", ""))
    tail = (j.get("log") or "").splitlines()[-20:]
    if tail:
        typer.echo("---- 日志（最后 20 行）----\n" + "\n".join(tail))


@job_app.command("show")
def job_show(job_id: int):
    _emit(_call("GET", f"/api/jobs/{job_id}"), _print_job)


@job_app.command("cancel")
def job_cancel(job_id: int):
    _emit(_call("POST", f"/api/jobs/{job_id}/cancel"),
          lambda r: typer.echo("已取消" if r["ok"] else "没能取消（任务可能已经结束）"))


@job_app.command("retry")
def job_retry(job_id: int):
    _emit(_call("POST", f"/api/jobs/{job_id}/retry", json={}),
          lambda r: typer.echo(f"已重新提交为任务 #{r['job_id']}"))


@job_app.command("input")
def job_input(job_id: int,
              file: Path = typer.Option(..., "--file", "-f", exists=True, dir_okay=False,
                                        help="模型输出（UTF-8 文本）")):
    _emit(_call("POST", f"/api/jobs/{job_id}/input", json={"text": file.read_text(encoding="utf-8")}),
          lambda j: typer.echo(f"已提交，任务 #{j['id']} 重新排队"))


# ------------------------------------------------------------ 候选、队列、体检
@candidate_app.command("select")
def candidate_select(candidate_id: int):
    _emit(_call("POST", f"/api/candidates/{candidate_id}/select"),
          lambda c: typer.echo(f"已选定候选 #{c['id']}：{c['file_path']}"))


def _print_queue(q: dict) -> None:
    c = q["counts"]
    typer.echo(f"{'已暂停' if q['paused'] else '运行中'}  排队 {c['queued']}  运行 {c['running']}  "
               f"等待输入 {c['waiting_input']}  当前模型族 {q.get('current_family') or '-'}")


@queue_app.command("status")
def queue_status():
    _emit(_call("GET", "/api/queue"), _print_queue)


@queue_app.command("pause")
def queue_pause():
    _emit(_call("POST", "/api/queue/pause"), _print_queue)


@queue_app.command("resume")
def queue_resume():
    _emit(_call("POST", "/api/queue/resume"), _print_queue)


def _print_doctor(rows: list[dict]) -> None:
    for r in rows:
        typer.echo(f"{ICON.get(r['status'], '?')} [{r['group']}] {r['name']}：{r['detail']}")


@app.command("doctor")
def doctor():
    rows = _call("GET", "/api/doctor")
    _emit(rows, _print_doctor)
    if any(r["status"] == "fail" for r in rows):
        raise typer.Exit(1)


def main() -> None:
    """console script 入口：Windows 控制台默认 GBK/cp1252 时中文会乱码或报错，统一改成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    app()


if __name__ == "__main__":
    main()
