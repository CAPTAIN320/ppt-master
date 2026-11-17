"""Offline tests for the isolated slide sub-agent, llm_utils and history compaction.

Run:  PPT_MASTER_ROOT=<repo> python3 web/tests/test_slide_subagent.py
(also pytest-compatible).  No network: the LLM client is a stub.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("PPT_MASTER_ROOT", str(REPO))
sys.path.insert(0, str(REPO))

try:  # allow running without the openai package installed (no network used)
    import openai  # noqa: F401
except ImportError:
    import types
    _m = types.ModuleType("openai"); _m.AsyncOpenAI = object; sys.modules["openai"] = _m

if not Path("/app").exists():  # host run: job_store opens /app/data at import time → stub it
    import types as _t
    _js = _t.ModuleType("web.job_store"); _js.JobStore = object; sys.modules["web.job_store"] = _js

from web import agent_runner, llm_utils, slide_subagent as ss  # noqa: E402
from web.tools import ToolContext, dispatch_tool, get_tool_definitions  # noqa: E402

EXAMPLE = REPO / "examples" / "ppt169_cangzhuo"

GOOD_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" width="1280" height="720">'
    '<rect width="1280" height="720" fill="#F5F1E8"/>'
    '<text x="80" y="140" font-family="Arial" font-size="40" fill="#1A1A1A">Title</text></svg>'
)
BAD_SVG = GOOD_SVG.replace("<rect", "<style>.a{}</style><rect", 1)


class FakeStore:
    def __init__(self):
        self.events, self.logs, self.project_path, self.cancelled, self.increments = [], [], None, False, 0

    def is_cancelled(self, job_id):
        return self.cancelled

    def set_project_path(self, job_id, p):
        self.project_path = p

    async def push_event(self, job_id, ev):
        self.events.append(ev)

    async def append_log(self, job_id, text):
        self.logs.append(text)

    def get_confirm_result(self, job_id):
        return None

    def increment_slide_count(self, job_id):  # must never be called by the tools
        self.increments += 1


class FakeClient:
    """Streams (or not) scripted replies. replies: list of (text, finish_reason) or Exception."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.calls.append(kw)
        assert "tools" not in kw, "sub-agent calls must be tool-less"
        r = self.replies.pop(0) if self.replies else (GOOD_SVG + "<notes>Hello there.</notes>", "stop")
        if isinstance(r, Exception):
            raise r
        text, fin = r if isinstance(r, tuple) else (r, "stop")
        if kw.get("stream"):
            async def gen():
                half = len(text) // 2
                for piece in (text[:half], text[half:]):
                    yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=piece), finish_reason=None)])
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason=fin)])
            return gen()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=fin)])


def _make_project(tmp: Path, keep_svgs: bool = False) -> Path:
    proj = tmp / "projects" / "demo_ppt169"
    proj.mkdir(parents=True)
    shutil.copy(EXAMPLE / "design_spec.md", proj / "design_spec.md")
    shutil.copy(EXAMPLE / "spec_lock.md", proj / "spec_lock.md")
    (proj / "svg_output").mkdir()
    if keep_svgs:
        for p in sorted((EXAMPLE / "svg_output").glob("*.svg"))[:2]:
            shutil.copy(p, proj / "svg_output" / p.name)
    return proj


def _setup(tmp: Path):
    ss.PROJECTS_DIR = tmp / "projects"


def _run(coro):
    return asyncio.run(coro)


# ── parsing / prompt ──────────────────────────────────────────────────────────


def test_parse_outline_and_lock_and_messages():
    spec = (EXAMPLE / "design_spec.md").read_text(encoding="utf-8")
    outline = ss.parse_outline(spec)
    assert sorted(outline) == list(range(1, 15)), sorted(outline)
    assert outline[3].name == "烧烤摊故事" and "#### Slide 03" in outline[3].text
    assert outline[1].layout.startswith("Full-bleed")
    # variations
    alt = "### Slide 01 - A\n- **Title**: X\n\n#### Page 02: B\n- **Core message**: y\n"
    o2 = ss.parse_outline(alt)
    assert o2[1].name == "A" and o2[1].title == "X" and o2[2].core_message == "y"

    raw, info = ss.read_spec_lock(EXAMPLE)
    assert info["viewBox"] == "0 0 1280 720" and info["page_charts"].get("P11") == "pros_cons_chart"
    assert "cover_bg" in info["images"]

    with tempfile.TemporaryDirectory() as t:
        proj = _make_project(Path(t), keep_svgs=True)
        msgs = ss.build_slide_messages(proj, 3, "03_x.svg", "make it bold", True, {"a": 1})
        assert [m["role"] for m in msgs] == ["system", "user"]
        sysm, user = msgs[0]["content"], msgs[1]["content"]
        assert "Output Contract" in sysm and "Anti-Overlap" in sysm and "Banned" in sysm.replace("banned", "Banned")
        for tag in ("<spec_lock.md>", "<design_spec.md>", "<image_facts>", "<style_anchors>", "<this_page>", "<user_confirmations>"):
            assert tag in user, tag
        assert "#### Slide 03" in user and "make it bold" in user and "Prev: P02" in user and "Next: P04" in user
        assert sum(len(m["content"]) for m in msgs) < ss.MAX_PROMPT_CHARS
        # style anchors exclude the target file itself
        assert ss.style_anchors(proj, "01_cover.svg")[0][0] != "01_cover.svg"


# ── single slide ──────────────────────────────────────────────────────────────


def test_generate_slide_normal_and_regenerate():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t); _setup(tmp); proj = _make_project(tmp)
        store, client = FakeStore(), FakeClient([(GOOD_SVG + "<notes>Welcome everyone. This is the cover.</notes>", "stop")])
        ctx = ToolContext(client=client, model="claude-test")
        res = _run(dispatch_tool("generate_slide_svg", {"project_path": str(proj), "slide_number": 1}, "j", store, ctx=ctx))
        assert res["status"] == "ok", res
        assert len(json.dumps(res)) < 1024 and "<svg" not in json.dumps(res)
        svg_path = Path(res["svg_path"])
        assert svg_path.name.startswith("01_") and svg_path.read_text(encoding="utf-8").startswith("<svg")
        assert (proj / "notes" / f"{svg_path.stem}.md").read_text().startswith("Welcome")
        assert store.project_path == str(proj.resolve())
        assert [e["type"] for e in store.events] == ["slide_ready"] and "regenerated" not in store.events[0]
        assert store.events[0]["slide"] == 1 and store.increments == 0
        assert client.calls[0]["stream"] is True
        # regenerate → flagged
        client.replies = [(GOOD_SVG, "stop")]
        res2 = _run(dispatch_tool("generate_slide_svg", {"project_path": str(proj), "slide_number": 1, "extra_instructions": "bolder"}, "j", store, ctx=ctx))
        assert res2["status"] == "ok" and store.events[-1].get("regenerated") is True


def test_gemini_non_streaming_and_path_guard():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t); _setup(tmp); proj = _make_project(tmp)
        store, client = FakeStore(), FakeClient([(GOOD_SVG, "stop")])
        ctx = ToolContext(client=client, model="gemini-3.1-pro-preview")
        res = _run(ss.generate_slide_svg({"project_path": str(proj), "slide_number": 2, "with_notes": False}, job_id="j", store=store, ctx=ctx))
        assert res["status"] == "ok" and client.calls[0]["stream"] is False
        bad = _run(ss.generate_slide_svg({"project_path": "/etc", "slide_number": 1}, job_id="j", store=store, ctx=ctx))
        assert bad["status"] == "failed"
        bad = _run(ss.generate_slide_svg({"project_path": str(proj / ".." / ".."), "slide_number": 1}, job_id="j", store=store, ctx=ctx))
        assert bad["status"] == "failed"


def test_truncation_recovery_and_double_truncation():
    cut = GOOD_SVG[: len(GOOD_SVG) - 40]
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t); _setup(tmp); proj = _make_project(tmp)
        store = FakeStore()
        client = FakeClient([(cut, "length"), (GOOD_SVG[len(cut):], "stop")])
        ctx = ToolContext(client=client, model="claude-test")
        res = _run(ss.generate_slide_svg({"project_path": str(proj), "slide_number": 1, "with_notes": False}, job_id="j", store=store, ctx=ctx))
        assert res["status"] == "ok", res
        assert len(client.calls) == 2 and client.calls[1]["messages"][-2]["role"] == "assistant"
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t); _setup(tmp); proj = _make_project(tmp)
        store = FakeStore()
        client = FakeClient([(cut, "length"), (cut, "length"), (cut, "length")])
        ctx = ToolContext(client=client, model="claude-test")
        res = _run(ss.generate_slide_svg({"project_path": str(proj), "slide_number": 1}, job_id="j", store=store, ctx=ctx))
        assert res["status"] == "failed" and "truncated twice" in res["error"] and not store.events
        assert not list((proj / "svg_output").glob("*.svg"))


def test_self_correction_once():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t); _setup(tmp); proj = _make_project(tmp)
        store = FakeStore()
        client = FakeClient([(BAD_SVG, "stop"), (GOOD_SVG, "stop")])
        ctx = ToolContext(client=client, model="claude-test")
        res = _run(ss.generate_slide_svg({"project_path": str(proj), "slide_number": 1, "with_notes": False}, job_id="j", store=store, ctx=ctx))
        assert res["corrected"] is True and res["status"] == "ok", res
        assert len(client.calls) == 2 and "Fix ONLY" in client.calls[1]["messages"][-1]["content"]
        assert "<style>" not in Path(res["svg_path"]).read_text()
        # still failing after the single pass → kept, ok_with_errors, exactly 2 calls
        client2 = FakeClient([(BAD_SVG, "stop"), (BAD_SVG, "stop")])
        ctx2 = ToolContext(client=client2, model="claude-test")
        res2 = _run(ss.generate_slide_svg({"project_path": str(proj), "slide_number": 2, "with_notes": False}, job_id="j", store=store, ctx=ctx2))
        assert res2["status"] == "ok_with_errors" and res2["quality"]["errors"] >= 1 and len(client2.calls) == 2


def test_cancellation():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t); _setup(tmp); proj = _make_project(tmp)
        store = FakeStore(); store.cancelled = True
        ctx = ToolContext(client=FakeClient([]), model="claude-test")
        res = _run(ss.generate_slide_svg({"project_path": str(proj), "slide_number": 1}, job_id="j", store=store, ctx=ctx))
        assert res["status"] == "cancelled" and not store.events


# ── batch ─────────────────────────────────────────────────────────────────────


def test_batch_order_anchors_skip_notes():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t); _setup(tmp); proj = _make_project(tmp)
        store = FakeStore()

        class SlowClient(FakeClient):
            async def _create(self, **kw):
                # later slides finish first → exercises ordered emission
                n = len(self.calls)
                await asyncio.sleep(0.05 if n < 2 else max(0.0, 0.2 - 0.03 * n))
                return await super()._create(**kw)

        client = SlowClient([])
        client.chat = SimpleNamespace(completions=SimpleNamespace(create=client._create))
        ctx = ToolContext(client=client, model="claude-test")
        res = _run(dispatch_tool("generate_slides_batch", {"project_path": str(proj), "slide_numbers": [1, 2, 3, 4, 5], "max_concurrency": 3}, "j", store, ctx=ctx))
        assert res["status"] == "ok" and [g["slide"] for g in res["generated"]] == [1, 2, 3, 4, 5], res
        assert res["notes_total"] is True and (proj / "notes" / "total.md").is_file()
        assert [e["slide"] for e in store.events] == [1, 2, 3, 4, 5] and store.increments == 0
        # first two calls were serial (anchors); calls 3+ saw style anchors in their prompt
        later_prompt = next(c for c in client.calls if "<style_anchors>" in c["messages"][-1]["content"])
        assert later_prompt
        assert len(json.dumps(res)) < 1500
        # idempotent resume: nothing to do, skipped
        n_calls = len(client.calls)
        res2 = _run(ss.generate_slides_batch({"project_path": str(proj), "slide_numbers": [1, 2, 3]}, job_id="j", store=store, ctx=ctx))
        assert res2["skipped"] == [1, 2, 3] and not res2["generated"] and len(client.calls) == n_calls
        # overwrite → regenerated flagged
        res3 = _run(ss.generate_slides_batch({"project_path": str(proj), "slide_numbers": [2], "overwrite": True}, job_id="j", store=store, ctx=ctx))
        assert res3["generated"][0]["slide"] == 2 and store.events[-1].get("regenerated") is True


def test_batch_partial_failure_all():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t); _setup(tmp); proj = _make_project(tmp)
        store = FakeStore()
        client = FakeClient([RuntimeError("boom")] + [(GOOD_SVG, "stop")] * 20)
        ctx = ToolContext(client=client, model="claude-test")
        res = _run(ss.generate_slides_batch({"project_path": str(proj), "slide_numbers": "all", "max_concurrency": 6, "with_notes": False}, job_id="j", store=store, ctx=ctx))
        assert res["status"] == "partial" and len(res["failed"]) == 1 and len(res["generated"]) == 13, res
        assert res["notes_total"] is False


# ── llm_utils ─────────────────────────────────────────────────────────────────


def test_call_llm_text_retries():
    class Err(Exception):
        def __init__(self, status, body=""):
            self.status_code = status
            self.response = SimpleNamespace(status_code=status, text=body)

    llm_utils._BACKOFF_SECONDS = (0.01, 0.01, 0.01)
    c = FakeClient([Err(400, "max_tokens too large"), ("ok", "stop")])
    text, fin = _run(llm_utils.call_llm_text(c, "claude-x", [{"role": "user", "content": "hi"}]))
    assert text == "ok" and c.calls[0]["max_tokens"] == 32000 and c.calls[1]["max_tokens"] == 16000
    c = FakeClient([Err(429), Err(503), ("ok2", "stop")])
    text, _ = _run(llm_utils.call_llm_text(c, "claude-x", [{"role": "user", "content": "hi"}]))
    assert text == "ok2" and len(c.calls) == 3
    c = FakeClient([Err(401)])
    try:
        _run(llm_utils.call_llm_text(c, "claude-x", []))
        raise AssertionError("should raise")
    except Err:
        pass
    # re-exports preserved
    assert agent_runner._is_gemini_model("Gemini-3") and agent_runner._max_tokens_for("x") == 32000
    assert agent_runner._is_token_limit_error is llm_utils._is_token_limit_error


def test_cancel_during_call():
    async def go():
        class Slow:
            def __init__(self):
                self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

            async def create(self, **kw):
                await asyncio.sleep(30)

        store = FakeStore()
        task = asyncio.ensure_future(llm_utils.call_llm_text(Slow(), "gemini-x", [], job_id="j", store=store))
        await asyncio.sleep(0.1)
        store.cancelled = True
        try:
            await asyncio.wait_for(task, 5)
            raise AssertionError("expected cancel")
        except llm_utils.LLMCancelled:
            pass

    _run(go())


# ── tools / prompt ────────────────────────────────────────────────────────────


def test_tool_registration_and_flag():
    names = [t["function"]["name"] for t in get_tool_definitions()]
    assert "generate_slide_svg" in names and "generate_slides_batch" in names
    os.environ["SLIDE_SUBAGENT"] = "0"
    try:
        names = [t["function"]["name"] for t in get_tool_definitions()]
        assert "generate_slide_svg" not in names
        from web import skill_prompt as sp
        assert "generate_slides_batch" not in sp.get_system_prompt()
        res = _run(dispatch_tool("generate_slide_svg", {}, "j", FakeStore(), ctx=None))
        assert "error" in res
    finally:
        os.environ.pop("SLIDE_SUBAGENT", None)
    res = _run(dispatch_tool("generate_slide_svg", {"project_path": "x", "slide_number": 1}, "j", FakeStore(), ctx=None))
    assert "error" in res


def test_skill_md_patch():
    from web import skill_prompt as sp
    sp._SKILL_MD_PATH = REPO / "skills" / "ppt-master" / "SKILL.md"
    t = sp.get_skill_md()
    assert "generate_slides_batch" in t and "MUST stay in the current main agent" not in t and "### Step 7" in t
    assert "generate_slides_batch" in sp.get_system_prompt()
    # marker miss → append, no crash
    out = sp._patch_step6("no markers here")
    assert out.startswith("no markers here") and "generate_slides_batch" in out


# ── compaction ────────────────────────────────────────────────────────────────


def _mk_history(model_sig: bool = False):
    def call(cid, name, args, **extra):
        return {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}, **extra}

    big_ref = "R" * 90000
    svg = "<svg>" + "x" * 15000 + "</svg>"
    msgs = [
        {"role": "user", "content": "SKILL" * 100},
        {"role": "assistant", "content": "ready"},
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": None, "tool_calls": [call("c1", "read_file", {"path": "/app/skills/ppt-master/references/strategist.md"}, **({"thought_signature": "SIG"} if model_sig else {}))]},
        {"role": "tool", "tool_call_id": "c1", "content": json.dumps({"content": big_ref, "path": "/app/skills/ppt-master/references/strategist.md"})},
        {"role": "assistant", "content": None, "tool_calls": [call("c2", "write_file", {"path": "/app/projects/p/svg_output/01_a.svg", "content": svg}, **({"thought_signature": "SIG2"} if model_sig else {}))]},
        {"role": "tool", "tool_call_id": "c2", "content": json.dumps({"written": "x", "bytes": len(svg)})},
        {"role": "assistant", "content": None, "tool_calls": [call("c3", "read_file", {"path": "/app/projects/p/design_spec.md"})]},
        {"role": "tool", "tool_call_id": "c3", "content": json.dumps({"content": "S" * 5000})},
        {"role": "assistant", "content": None, "tool_calls": [call("c4", "read_file", {"path": "/app/projects/p/design_spec.md"})]},
        {"role": "tool", "tool_call_id": "c4", "content": json.dumps({"content": "S" * 5000})},
        {"role": "assistant", "content": None, "tool_calls": [call("c5", "list_dir", {"path": "/app"})]},
        {"role": "tool", "tool_call_id": "c5", "content": json.dumps({"entries": []})},
    ]
    return msgs


def test_compact_history():
    with tempfile.TemporaryDirectory() as t:
        proj = Path(t) / "p"; proj.mkdir()
        (proj / "design_spec.md").write_text("x"); (proj / "spec_lock.md").write_text("x")
        msgs = _mk_history(model_sig=True)
        before = agent_runner._history_chars(msgs)
        freed = agent_runner._compact_history(msgs, str(proj), "claude-sonnet")
        assert freed > 100000 and agent_runner._history_chars(msgs) == before - freed
        agent_runner._assert_pairing(msgs)
        assert json.loads(msgs[4]["content"])["compacted"] is True
        assert "compacted" in msgs[8]["content"] and "S" * 100 not in msgs[8]["content"]  # superseded read
        assert msgs[10]["content"] == json.dumps({"content": "S" * 5000})  # latest read untouched
        a = json.loads(msgs[5]["tool_calls"][0]["function"]["arguments"])
        assert a["path"].endswith("01_a.svg") and "written to disk" in a["content"]
        assert msgs[5]["tool_calls"][0]["thought_signature"] == "SIG2" and msgs[5]["tool_calls"][0]["id"] == "c2"
        # idempotent
        snap = json.dumps(msgs)
        assert agent_runner._compact_history(msgs, str(proj), "claude-sonnet") == 0 and json.dumps(msgs) == snap
        # spec not ready → strategist read (90K, but too recent vs tail) behaviour: not compacted when spec missing & recent
        msgs2 = _mk_history()
        agent_runner._compact_history(msgs2, str(Path(t) / "missing"), "claude-sonnet")
        # Gemini guard: tool results compacted, tool-call args untouched
        msgs3 = _mk_history(model_sig=True)
        orig_args = msgs3[5]["tool_calls"][0]["function"]["arguments"]
        freed3 = agent_runner._compact_history(msgs3, str(proj), "gemini-3.1-pro-preview")
        assert freed3 > 80000 and msgs3[5]["tool_calls"][0]["function"]["arguments"] == orig_args
        agent_runner._assert_pairing(msgs3)
        # protected tail: last tool-calling turn is never touched
        msgs4 = _mk_history()
        msgs4.append({"role": "assistant", "content": None, "tool_calls": [{"id": "c9", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": "/app/skills/ppt-master/references/executor-base.md"})}}]})
        big = json.dumps({"content": "E" * 40000})
        msgs4.append({"role": "tool", "tool_call_id": "c9", "content": big})
        agent_runner._compact_history(msgs4, str(proj), "claude-sonnet")
        assert msgs4[-1]["content"] == big
        agent_runner._assert_pairing(msgs4)


def test_system_prompt_size_realistic():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t); proj = _make_project(tmp, keep_svgs=True)
        msgs = ss.build_slide_messages(proj, 5, None, "", True, None)
        total = sum(len(m["content"]) for m in msgs)
        print(f"[info] slide prompt ≈ {total/1000:.0f}K chars (system {len(msgs[0]['content'])/1000:.0f}K)")
        assert 20000 < total < ss.MAX_PROMPT_CHARS


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            import traceback
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
