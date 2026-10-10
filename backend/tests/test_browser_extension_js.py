"""browser-extension/sites/*.js 和 backend/engines/browser/sites/*.py 是同一份选择器和文案正则：
网站改版时两边都要改，这里防止只改了一边。需要 node（前端本来就要），没有就跳过。"""
import json
import shutil
import subprocess
from dataclasses import fields

import pytest

from backend.engines.browser.errors import EXT_DIR
from backend.engines.browser.sites import SITES

NODE = shutil.which("node")
SCRIPT = """
const { SITES } = await import(process.argv[1]);
const out = {};
for (const [k, s] of Object.entries(SITES)) {
  out[k] = Object.fromEntries(Object.entries(s).filter(([, v]) => typeof v !== 'function').map(([n, v]) =>
    [n, Array.isArray(v) ? v.map((x) => (x instanceof RegExp ? x.source : x)) : v]));
}
console.log(JSON.stringify(out));
"""
# Python 有、扩展里没有（或含义不同）的字段
PY_ONLY = {"download_buttons", "prefer_download_button", "extra"}


@pytest.fixture(scope="module")
def js_sites():
    if NODE is None:
        pytest.skip("没有 node")
    url = (EXT_DIR / "sites" / "index.js").as_uri()
    r = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT, url], capture_output=True, text=True, check=False,
                       encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("key", sorted(SITES))
def test_extension_site_matches_python(js_sites, key):
    py, js = SITES[key], js_sites[key]
    for f in fields(py):
        if f.name in PY_ONLY:
            continue
        want = getattr(py, f.name)
        if f.name.endswith("_patterns"):
            want = [p.pattern for p in want]
            assert all(p.flags & 2 for p in getattr(py, f.name))   # re.I，扩展里也都是 /i
        elif isinstance(want, tuple):
            want = list(want)
        assert js[f.name] == want, f"{key}.{f.name} 两边不一致：扩展 {js[f.name]!r}，后端 {want!r}"


def test_manifest_permissions():
    m = json.loads((EXT_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert m["manifest_version"] == 3 and m["background"]["type"] == "module"
    assert {"tabs", "scripting", "storage", "alarms", "cookies"} <= set(m["permissions"])
    hosts = set(m["host_permissions"])
    for s in SITES.values():
        assert f"https://{s.host}/*" in hosts
    assert "https://*.oaiusercontent.com/*" in hosts and "https://*.googleusercontent.com/*" in hosts
