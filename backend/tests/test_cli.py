import json

import pytest
from typer.testing import CliRunner

import cli.studio as studio
from backend.domain.models import Job, Shot
from backend.domain.shots import renumber_shots
from backend.domain.slots import add_candidate, get_or_create_slot

runner = CliRunner()


@pytest.fixture
def cli(api, monkeypatch):
    monkeypatch.setattr(studio, "_client", lambda: api)
    return lambda *args: runner.invoke(studio.app, [str(a) for a in args])


def test_project_create_and_list(cli):
    r = cli("--json", "project", "create", "提词员", "--aspect", "9:16")
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["slug"] == "提词员"
    r = cli("project", "list")
    assert "提词员" in r.stdout and "zimage / h3_video" in r.stdout


def test_episode_import_run_and_jobs(cli, tmp_path):
    cli("project", "create", "demo")
    f = tmp_path / "梗概.md"
    f.write_text("林夏发现提词器被改了。", encoding="utf-8")
    r = cli("--json", "episode", "import", "demo", "--file", f, "--seconds", "30")
    assert json.loads(r.stdout)["number"] == 1
    r = cli("--json", "run", "script", "demo", "--episode", "1")
    ids = json.loads(r.stdout)["job_ids"]
    assert len(ids) == 1
    r = cli("--json", "jobs", "--status", "queued")
    assert [j["id"] for j in json.loads(r.stdout)] == ids
    assert "script" in cli("jobs").stdout
    assert cli("job", "cancel", ids[0]).exit_code == 0
    assert json.loads(cli("--json", "job", "show", ids[0]).stdout)["status"] == "canceled"
    r = cli("--json", "job", "retry", ids[0])
    assert json.loads(r.stdout)["job_id"] != ids[0]


def test_http_errors_exit_1(cli):
    r = cli("run", "keyframes", "nope", "--episode", "1")
    assert r.exit_code == 1 and "找不到项目" in r.output


def test_unreachable_server_exits_2(monkeypatch):
    monkeypatch.setenv("STUDIO_URL", "http://127.0.0.1:9")
    r = runner.invoke(studio.app, ["project", "list"])
    assert r.exit_code == 2 and "连不上" in r.output


def test_episode_show(cli, api):
    cli("project", "create", "demo")
    ep = api.post("/api/projects/demo/episodes", json={"source_text": "x"}).json()
    with api.app.state.services.db.session() as s:
        s.add(Shot(episode_id=ep["id"], order=0, action="林夏抬头", duration=5))
        s.flush()
        renumber_shots(s, ep["id"])
    out = cli("episode", "show", "demo", 1).stdout
    assert "S01" in out and "林夏抬头" in out and "·" in out


def test_candidate_select(cli, api):
    cli("project", "create", "demo")
    sv = api.app.state.services
    with sv.db.session() as s:
        slot = get_or_create_slot(s, "shot", 1, "first_frame")
        add_candidate(s, slot, file_path="demo/media/first_frame/a.png", engine="zimage",
                      params={}, input_hash="h", job_id=None)
        cid = add_candidate(s, slot, file_path="demo/media/first_frame/b.png", engine="zimage",
                            params={}, input_hash="h", job_id=None).id
    r = cli("candidate", "select", cid)
    assert r.exit_code == 0 and "已选定" in r.output


def test_job_input(cli, api, tmp_path):
    sv = api.app.state.services
    jid = api.app.state.manager.submit("script", {"episode_id": 1})
    with sv.db.session() as s:
        j = s.get(Job, jid)
        j.status = "waiting_input"
        j.result = {"input_request": {"key": "script_structure"}}
    f = tmp_path / "结果.json"
    f.write_text('{"title": "t"}', encoding="utf-8")
    assert cli("job", "input", jid, "--file", f).exit_code == 0
    d = api.get(f"/api/jobs/{jid}").json()
    assert d["status"] == "queued" and d["payload"]["manual"]["script_structure"] == '{"title": "t"}'


def test_doctor_exit_code(cli):
    r = cli("doctor")
    assert r.exit_code == 1 and "ComfyUI" in r.output


def test_server_timeout_message(monkeypatch):
    import httpx

    def boom(request):
        raise httpx.ReadTimeout("timed out", request=request)

    monkeypatch.setattr(studio, "_client", lambda: httpx.Client(
        base_url="http://127.0.0.1:8700", transport=httpx.MockTransport(boom)))
    r = runner.invoke(studio.app, ["project", "list"])
    assert r.exit_code == 2 and "工作台响应超时" in r.output and "连不上" not in r.output


def test_main_switches_console_to_utf8(monkeypatch):
    import io
    import sys

    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    called = []
    monkeypatch.setattr(studio, "app", lambda: called.append(sys.stdout.encoding))
    studio.main()
    assert called == ["utf-8"] and sys.stderr.encoding == "utf-8"
    print("提词员")  # cp1252 编不了中文；切到 utf-8 后不再抛 UnicodeEncodeError


def test_console_script_points_at_main():
    import tomllib
    from pathlib import Path

    meta = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    assert meta["project"]["scripts"]["studio"] == "cli.studio:main"


def test_jobs_watch_stops_on_waiting_input(cli, api):
    jid = api.app.state.manager.submit("script", {"episode_id": 1})
    with api.app.state.services.db.session() as s:
        s.get(Job, jid).status = "waiting_input"
    r = cli("jobs", "--watch")
    assert r.exit_code == 0
    assert "有任务在等待人工输入，用 studio job show <id> 查看" in r.output
    assert "队列已空" not in r.output


def test_jobs_watch_empty_queue(cli):
    r = cli("jobs", "--watch")
    assert r.exit_code == 0 and "队列已空" in r.output
