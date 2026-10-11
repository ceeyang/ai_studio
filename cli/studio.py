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
character_app = typer.Typer(help="角色", no_args_is_help=True)
subtitles_app = typer.Typer(help="字幕（中文 / 高棉语）", no_args_is_help=True)
app.add_typer(project_app, name="project")
app.add_typer(episode_app, name="episode")
app.add_typer(job_app, name="job")
app.add_typer(candidate_app, name="candidate")
app.add_typer(queue_app, name="queue")
app.add_typer(character_app, name="character")
app.add_typer(subtitles_app, name="subtitles")

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
                   image_engine: Optional[str] = typer.Option(
                       None, "--image-engine", help="不给就按设置页的默认图片来源"),
                   video_engine: Optional[str] = typer.Option(
                       None, "--video-engine", help="不给就按设置页的默认视频来源")):
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
def run(stage: str = typer.Argument(..., help="script / storyboard / keyframes / videos / export / all，"
                                              "或 fit-khmer（按高棉语时长改写中文，不在 all 里）"),
        project: str = typer.Argument(...),
        episode: int = typer.Option(1, "--episode", "-e", help="集数"),
        shots: Optional[str] = typer.Option(None, help="只跑这些镜头，逗号分隔，如 S01,S03"),
        candidates: int = typer.Option(1, help="每个镜头出几张首帧候选（1–4）"),
        force: bool = typer.Option(False, help="输入没变也重新生成"),
        yes: bool = typer.Option(False, "--yes", "-y",
                                 help="剧本/分镜重跑会删掉有选定首帧/视频或改过的镜头时，确认照删"),
        subtitles: str = typer.Option("none", help="导出：烧字幕 none / zh / zh+km（中文在上、高棉语在下）"),
        dub: str = typer.Option("off", help="导出：off / km（高棉语外挂配音，原音轨压低当底）"),
        end_card: bool = typer.Option(False, "--end-card/--no-end-card", help="导出：接项目片尾卡"),
        trim: bool = typer.Option(True, "--trim/--no-trim", help="导出：按分镜时长裁掉台词后的多余尾巴"),
        bed: bool = typer.Option(True, "--bed/--no-bed", help="导出：配音时保留压低的原声底；--no-bed 原音轨整条静音"),
        align: str = typer.Option("timeline", help="导出：配音放在 timeline（台词时间）或 detected（H3 实际开口处）"),
        then: Optional[str] = typer.Option(None, help="本阶段完成后接着跑，逗号分隔，如 videos,export")):
    if subtitles not in ("none", "zh", "zh+km") or dub not in ("off", "km") \
            or align not in ("timeline", "detected"):
        typer.echo("--subtitles 只能是 none / zh / zh+km，--dub 只能是 off / km，"
                   "--align 只能是 timeline / detected", err=True)
        raise typer.Exit(2)
    stage = stage.replace("-", "_")
    nxt = [x.strip() for x in (then or "").split(",") if x.strip()]
    ep = _call("GET", f"/api/projects/{project}/episodes/by-number/{episode}")
    body: dict = {"stage": stage, "candidates": candidates, "force": force}
    if stage in ("script", "storyboard", "all") and not force:
        _print_impact(ep["id"], stage)
    if yes:
        body["confirm"] = True
    if shots:
        body["shots"] = [x.strip() for x in shots.split(",") if x.strip()]
    if nxt:
        body["then"] = nxt
    if "export" in (stage, *nxt) or stage == "all":
        body["export"] = {"subtitles": subtitles, "dub": dub, "end_card": end_card, "trim": trim,
                          "bed": bed, "align": align}
    def show(r: dict) -> None:
        typer.echo(f"已提交 {len(r['job_ids'])} 个任务：{', '.join(map(str, r['job_ids']))}")
        for w in r.get("warnings") or []:
            typer.echo(f"提醒：{w}", err=True)
        for n in r.get("notes") or []:
            typer.echo(f"说明：{n}", err=True)
        if stage == "all":
            typer.echo(ALL_GUIDE, err=True)

    _emit(_call("POST", f"/api/episodes/{ep['id']}/run", json=body), show)


# run all 不含定妆照（要人挑），也没有网页上的首帧确认门：用锁脸引擎时先把定妆照定好
ALL_GUIDE = ("说明：all = 剧本 → 分镜 → 首帧 → 视频 → 导出，不含定妆照，也不停下来确认首帧。"
             "图像引擎要用定妆照锁脸（如 h3_image）时，先跑 studio run script <项目> --then storyboard，"
             "在网页「角色」阶段抽好定妆照（或 studio character portrait 上传），"
             "再跑 studio run keyframes <项目> --then videos,export")


def impact_line(im: dict) -> str:
    """重跑影响写成一句话（和界面上的确认框同一个意思）。"""
    if not im.get("will_run"):
        line = "输入没变，会跳过"
    else:
        where = f"段落 {'、'.join(map(str, im['blocks']))}" if im.get("blocks") else "整集"
        line = f"会重跑{where}：删除 {im['shots_deleted']} 个镜头"
        extra = [f"{im[k]} 个{label}" for k, label in (("selected_videos", "有选定视频"),
                                                      ("selected_first_frames", "有选定首帧"),
                                                      ("edited_shots", "生成后改过"))
                 if im.get(k)]
        if extra:
            line += f"（其中 {'、'.join(extra)}）"
        if im.get("manual_kept"):
            line += f"，保留 {im['manual_kept']} 个手工镜头"
        if im.get("overwrites"):
            line += f"；会覆盖{'、'.join(im['overwrites'])}"
    if im.get("rules_outdated"):
        what = "剧本" if im.get("stage") == "script" else "分镜"
        line += f"；{what}规则已更新，要按新规则重出请加 --force"
    return line


def _print_impact(episode_id: int, stage: str) -> None:
    if STATE["json"]:
        return
    try:   # 只是提示：拿不到影响（比如还没有段落）就不打印，照常提交，由服务端报错
        r = _client().get(f"/api/episodes/{episode_id}/rerun-impact", params={"stage": stage})
    except httpx.HTTPError:
        return
    if r.status_code >= 400:
        return
    im = r.json()
    typer.echo(f"重跑影响（{stage}）：{impact_line(im)}", err=True)


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


# ------------------------------------------------------------ 角色
def _print_characters(cs: list[dict]) -> None:
    if not cs:
        typer.echo("（还没有角色）")
    for c in cs:
        typer.echo(f"#{c['id']:<5}{c['name']}	定妆照 {'√' if c.get('portrait_url') else '·'}")


@character_app.command("list")
def character_list(project: str):
    _emit(_call("GET", f"/api/projects/{project}/characters"), _print_characters)


@character_app.command("create")
def character_create(project: str, name: str,
                     description: str = typer.Option("", help="人物小传"),
                     visual_prompt: str = typer.Option("", "--visual-prompt", help="外貌提示词（英文）")):
    body = {"name": name, "description": description, "visual_prompt": visual_prompt}
    _emit(_call("POST", f"/api/projects/{project}/characters", json=body),
          lambda c: typer.echo(f"已创建角色 #{c['id']} {c['name']}"))


@character_app.command("portrait")
def character_portrait(project: str, name: str,
                       file: Path = typer.Option(..., "--file", "-f", exists=True, dir_okay=False,
                                                 help="定妆照（png / jpg / webp）")):
    ch = _find_character(project, name)
    with file.open("rb") as fh:
        out = _call("POST", f"/api/characters/{ch['id']}/portrait",
                    files={"file": (file.name, fh.read())})
    _emit(out, lambda c: typer.echo(f"已设定 {c['name']} 的定妆照：{c['portrait_url']}"))


@character_app.command("voice")
def character_voice(project: str, name: str,
                    voice: str = typer.Argument(..., help="Edge TTS 音色，如 km-KH-PisethNeural；传空串清除")):
    ch = _find_character(project, name)
    out = _call("PATCH", f"/api/characters/{ch['id']}", json={"voice": voice or None})
    _emit(out, lambda c: typer.echo(f"{c['name']} 的配音音色：{c['voice'] or '（无）'}"))


def _find_character(project: str, name: str) -> dict:
    chars = _call("GET", f"/api/projects/{project}/characters")
    ch = next((c for c in chars if c["name"] == name), None)
    if ch is None:
        typer.echo(f"项目 {project} 里没有角色 {name}，现有：{', '.join(c['name'] for c in chars) or '无'}",
                   err=True)
        raise typer.Exit(1)
    return ch


# ------------------------------------------------------------ 片尾卡
def _print_card(card: dict | None) -> None:
    if not card:
        typer.echo("（还没有片尾卡）")
        return
    typer.echo(f"片尾卡 {card['seconds']:g} 秒，底色 {card['background']}")
    for ln in card["lines"]:
        bold = " 粗" if ln.get("bold") else ""
        typer.echo(f"  {ln['text'] or '（空行）'}    [{ln.get('font') or '默认字体'} {ln['size']}px{bold}]")


@project_app.command("end-card")
def project_end_card(project: str,
                     file: Optional[Path] = typer.Option(None, "--file", "-f", exists=True, dir_okay=False,
                                                         help="片尾卡 JSON：{seconds, background, lines: "
                                                              "[{text, font, size, bold}]}；不给就显示当前设置")):
    if file is None:
        _emit(_call("GET", f"/api/projects/{project}/end-card"), _print_card)
        return
    body = json.loads(file.read_text(encoding="utf-8"))
    _emit(_call("PUT", f"/api/projects/{project}/end-card", json=body), _print_card)


# ------------------------------------------------------------ 字幕
REVIEW = {"ai_draft": "AI 译稿·待母语校对", "checked": "已校对"}


def _print_subtitles(ep: dict) -> None:
    for sh in ep["shots"]:
        for i, d in enumerate(sh["dialogue"], start=1):
            km = (d.get("km") or "").strip()
            state = REVIEW.get(d.get("km_review") or "", "") if km else "缺高棉语"
            typer.echo(f"{sh['code']}.{i:<3}{d['start']:>5.1f}–{d['end']:<5.1f}"
                       f"{d.get('speaker', '')}：{d['zh']}")
            typer.echo(f"{'':>20}{km or '—'}  [{state}]")


@subtitles_app.command("show")
def subtitles_show(project: str, episode: int = typer.Option(1, "--episode", "-e")):
    _emit(_call("GET", f"/api/projects/{project}/episodes/by-number/{episode}"), _print_subtitles)


@subtitles_app.command("translate")
def subtitles_translate(project: str, episode: int = typer.Option(1, "--episode", "-e"),
                        force: bool = typer.Option(False, help="已有译文的也重翻"),
                        glossary: Optional[list[str]] = typer.Option(None, help="固定译名 中文=高棉语，可重复")):
    ep = _call("GET", f"/api/projects/{project}/episodes/by-number/{episode}")
    terms = {}
    for g in glossary or []:
        k, sep, v = g.partition("=")
        if not sep or not k.strip() or not v.strip():
            typer.echo(f"--glossary 要写成 中文=高棉语：{g}", err=True)
            raise typer.Exit(2)
        terms[k.strip()] = v.strip()
    out = _call("POST", f"/api/episodes/{ep['id']}/subtitles/translate",
                json={"force": force, "glossary": terms})
    _emit(out, lambda r: typer.echo(f"已提交翻译任务 #{r['job_ids'][0]}。译文是 AI 草稿，发布前要请母语者校对"))


@subtitles_app.command("set")
def subtitles_set(project: str, shot: str = typer.Argument(..., help="镜头编号，如 S02"),
                  line: int = typer.Argument(..., help="第几句（从 1 起）"),
                  episode: int = typer.Option(1, "--episode", "-e"),
                  km: Optional[str] = typer.Option(None, help="高棉语"),
                  zh: Optional[str] = typer.Option(None, help="中文（改了会让视频过期）"),
                  start: Optional[float] = typer.Option(None, help="开始秒数（相对本镜）"),
                  end: Optional[float] = typer.Option(None, help="结束秒数（相对本镜）"),
                  checked: Optional[bool] = typer.Option(None, "--checked/--draft",
                                                         help="高棉语是否已由母语者校对")):
    ep = _call("GET", f"/api/projects/{project}/episodes/by-number/{episode}")
    sh = next((x for x in ep["shots"] if x["code"].upper() == shot.upper()), None)
    if sh is None or not 1 <= line <= len(sh["dialogue"]):
        typer.echo(f"找不到 {shot} 的第 {line} 句", err=True)
        raise typer.Exit(1)
    dialogue = [dict(d) for d in sh["dialogue"]]
    d = dialogue[line - 1]
    for key, val in (("km", km), ("zh", zh), ("start", start), ("end", end)):
        if val is not None:
            d[key] = val
    if checked is not None:
        d["km_review"] = "checked" if checked else "ai_draft"
    out = _call("PATCH", f"/api/shots/{sh['id']}", json={"dialogue": dialogue})
    _emit(out, lambda x: typer.echo(f"{x['code']}.{line} 已更新"))


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
