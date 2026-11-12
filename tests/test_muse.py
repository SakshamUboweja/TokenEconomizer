"""Muse Code (Meta): the session reader, its host adapter, usage, history import and packaging.

No Muse recording is committed (run outputs stay out of git), so every fixture is synthetic and
built here. Where each shape comes from:

  * the envelope, and the `started`, `assistant_message_committed` and `terminal` events --
    Meta's own example records (`schema/msp/transcripts/reconciliation-refold/records.jsonl` in
    the Muse Code SDK repository);
  * `runtime.session.metadata`, `route_facts`, `model_completed.usage`,
    `assistant_tool_calls_committed` (a JSON-string `args`), `tool_result_batch_committed`
    (`tool failed: ` for an error), a task's `started` with no prompt, a prompt carrying
    `source_run_record_id`, `session.end`, microsecond `recorded_at` -- a sanitized Muse 0.2.1
    session published by a third-party reader, field names copied exactly;
  * `retained_frame` / `retained_marker` lines and the `subagent/` layout -- third-party readers;
  * SessionStart and UserPromptSubmit payloads -- verbatim from Meta's hooks guide; the tool
    events' keys from its hook-events reference, with made-up values.
"""
import io
import json
import os
import subprocess
import time

import pytest

from tokeneconomizer import trace
from tokeneconomizer.plugin import accounting, denylist, history, hook, hosts, sidecall
from tokeneconomizer.plugin.context import LiveContext

SID = "0198f0aa-2222-7000-8000-0000000000bb"
FAIL = "tool failed: python: can't open file 'tests/runtests.py': No such file or directory"
OK = "Ran 3 tests in 0.01s\nOK"
TEST_CALLS = [("bash", {"command": "python tests/runtests.py"}, FAIL),
              ("bash", {"command": "python runtests.py"}, OK)]


class _Log:
    """A synthetic Muse session log, written record by record."""

    def __init__(self, sid=SID, t0=1_788_223_110_000_000):
        self.sid, self.seq, self.t = sid, 0, t0
        self.lines = []

    def rec(self, payload_type, payload):
        self.seq += 1
        self.t += 1000
        self.lines.append({"schema_version": 1, "id": f"r{self.seq}",
                           "stream": {"kind": "session", "id": self.sid}, "sequence": self.seq,
                           "recorded_at": self.t, "record_type": "event",
                           "durability": "durable", "causation_id": None,
                           "payload_type": payload_type, "payload_schema_version": 1,
                           "payload": payload})
        return self

    def run(self, event, **extra):
        return self.rec("runtime.session", dict({"kind": "run", "run_id": "run1",
                                                 "event": event}, **extra))

    def head(self, cwd, model="meta/muse-spark-1.2"):
        self.rec("runtime.session.metadata", {"kind": "metadata", "record": {
            "build": {"semver": "1.4.14"}, "model_id": model, "provider_id": "meta",
            "workspace_root": cwd}})
        return self.rec("runtime.session.route_facts", {"kind": "route_facts",
                                                         "record": {"cwd": cwd, "pid": 1}})

    def prompt(self, text):
        # Real prompts carry `source_run_record_id` too: it does not mark a replay.
        return self.run({"kind": "started", "prompt": text}, source_run_record_id="x",
                        source_run_record_sequence=1)

    def calls(self, calls, model="meta/muse-spark-1.2", usage=None):
        for i, (name, args, text) in enumerate(calls):
            cid = f"call_{self.seq}_{i}"
            self.run({"kind": "model_completed", "model": model, "usage": usage or {
                "input_tokens": 1000, "output_tokens": 50, "cached_tokens": 600,
                "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 10}})
            self.run({"kind": "assistant_tool_calls_committed", "message_id": "m",
                      "tool_calls": [{"args": json.dumps(args), "call_id": cid,
                                      "id": "fc_" + cid, "name": name}]})
            self.run({"kind": "tool_result_batch_committed", "batch_id": "b", "results": [
                {"text": text, "tool_call_id": cid, "tool_call_index": 0}]})
        return self

    def answer(self, text="Done.", terminal="completed"):
        self.run({"kind": "assistant_message_committed", "message_id": "m", "text": text})
        return self.run({"kind": "terminal", "reason": None, "terminal": terminal})

    def end(self):
        return self.rec("session.end", {"kind": "session_end", "record": {
            "exit_reason": "clean", "schema_version": 1, "session_id": self.sid}})

    def write(self, data_dir, day=("2026", "11", "01"), age_s=7200):
        folder = os.path.join(str(data_dir), "sessions", *day, self.sid)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "session.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(json.dumps(x) for x in self.lines) + "\n")
        old = time.time() - age_s
        os.utime(path, (old, old))
        return path


def _sid(i):
    return f"0198f0aa-2222-7000-8000-{i:012d}"


def _session(cwd, calls=TEST_CALLS, sid=SID):
    return _Log(sid).head(cwd).prompt("run the tests").calls(calls).answer().end()


def _env(state, data):
    return {"TOKENECONOMIZER_HOME": str(state), "TE_FOLD_SYNC": "1",
            "TE_MICROSKILLS": "envfacts", "TE_TOOLPRUNE": "off",
            "TE_MUSE_DATA_DIR": str(data), "PATH": os.environ.get("PATH", "")}


def _call(env, payload):
    out = io.StringIO()
    hook.main(["--host", "muse"], io.StringIO(json.dumps(payload)), out, env)
    return json.loads(out.getvalue())


# ---- the reader -----------------------------------------------------------------------------
def test_the_reader_writes_a_claude_transcript_with_claude_names(tmp_path):
    path = _session(str(tmp_path)).write(tmp_path / "data")
    out = str(tmp_path / "claude.jsonl")
    assert trace.muse_session_to_claude(path, out) == 2
    calls = trace.load_trajectory(out)
    assert [c[0] for c in calls] == ["Bash", "Bash"]
    assert calls[0][1]["command"] == "python tests/runtests.py"
    assert calls[0][3] is False and calls[1][3] is True        # `tool failed: ` is an error
    assert trace.call_cwds(out) == [str(tmp_path)] * 2
    lines = [json.loads(ln) for ln in open(out, encoding="utf-8")]
    assert lines[1]["message"]["model"] == "meta/muse-spark-1.2"


def test_file_tools_get_a_file_path_and_bad_args_are_kept_raw(tmp_path):
    log = _Log().head(str(tmp_path)).prompt("look")
    log.calls([("read_file", {"path": "a.py"}, "1|x"), ("search", {"pattern": "x"}, "a.py:1")])
    log.run({"kind": "assistant_tool_calls_committed", "tool_calls": [
        {"args": "{not json", "call_id": "c9", "name": "mcp__x__y"}]})
    path = log.write(tmp_path / "data")
    out = str(tmp_path / "c.jsonl")
    trace.muse_session_to_claude(path, out)
    calls = trace.load_trajectory(out)
    assert calls[0][0] == "Read" and calls[0][1]["file_path"] == "a.py"
    assert calls[1][0] == "Grep"
    assert calls[2][:2] == ("mcp__x__y", {"raw": "{not json"})


def test_unknown_lines_frames_and_a_truncated_tail_are_tolerated(tmp_path):
    path = _session(str(tmp_path)).write(tmp_path / "data")
    inner = _Log().run({"kind": "started", "prompt": "a framed prompt"}).lines[0]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"retained_marker": "omitted_live_only"}) + "\n")
        fh.write(json.dumps({"retained_frame": "x", "frame_schema_version": 1, "children": [
            {"child_index": 0, "record_json": json.dumps(inner)},
            {"child_index": 1, "record_json": "{broken"}]}) + "\n")
        fh.write(json.dumps({"payload_type": "runtime.session", "payload": {
            "kind": "run", "event": {"kind": "a_kind_from_the_future"}}}) + "\n")
        fh.write("[1, 2]\nnull\n\"text\"\n")
        fh.write('{"schema_version":1,"payload_type":"runtime.sess')       # cut mid-write
    assert trace.muse_prompt_count(path) == 2
    assert trace.muse_session_to_claude(path, str(tmp_path / "c.jsonl")) == 2


def test_a_missing_or_unreadable_log_reads_as_nothing(tmp_path):
    assert trace.muse_session_to_claude(str(tmp_path / "nope.jsonl"), str(tmp_path / "o")) is None
    assert trace.muse_prompt_count(str(tmp_path / "nope.jsonl")) == 0
    assert trace.muse_session_usage(str(tmp_path / "nope.jsonl"))["input_tokens"] is None
    (tmp_path / "bin.jsonl").write_bytes(b"\xff\xfe\x00garbage\n")
    assert trace.muse_prompt_count(str(tmp_path / "bin.jsonl")) == 0
    assert trace.muse_log_head(str(tmp_path / "dir")) == {"entrypoint": None}


def test_prompt_count_counts_typed_prompts_only(tmp_path):
    log = _session(str(tmp_path))
    log.rec("runtime.session", {"kind": "task", "task_id": "t1", "event": {"kind": "started"}})
    log.run({"kind": "started", "prompt": "<system-reminder>injected</system-reminder>"})
    log.run({"kind": "started", "prompt": "   "})
    log.rec("session.resumed", {"kind": "session_resumed", "record": {"session_id": SID}})
    log.prompt("and now the docs").answer().end()
    path = log.write(tmp_path / "data")
    assert trace.muse_prompt_count(path) == 2
    turns = list(trace.muse_prompt_turns(path))
    assert len(turns) == 2 and "run the tests" in turns[0][0]


def test_a_cancelled_turn_reads_as_interrupted(tmp_path):
    log = _Log().head(str(tmp_path)).prompt("go").calls(TEST_CALLS[:1]).run(
        {"kind": "terminal", "terminal": "cancelled", "reason": None})
    turn = next(trace.muse_prompt_turns(log.write(tmp_path / "data")))
    assert trace.claude_turn_interrupted(turn) is True


# ---- usage ------------------------------------------------------------------------------------
def test_usage_takes_cached_input_out_and_sums_child_logs(tmp_path):
    path = _session(str(tmp_path)).write(tmp_path / "data")
    child = _Log(_sid(9)).calls([("bash", {"command": "ls"}, "a")]).write(tmp_path / "x")
    os.makedirs(os.path.join(os.path.dirname(path), "subagent", _sid(9)))
    os.replace(child, os.path.join(os.path.dirname(path), "subagent", _sid(9), "session.jsonl"))
    u = trace.muse_session_usage(path)
    assert u["requests"] == 3
    assert u["input_tokens"] == 3 * 400 and u["cache_read_input_tokens"] == 3 * 600
    assert u["output_tokens"] == 3 * 50                 # reasoning is inside output already
    assert u["cache_creation_input_tokens"] == 0
    assert u["model"] == "meta/muse-spark-1.2"
    eff, priced_by = accounting.effective(u)
    assert eff > 0 and priced_by == "fallback"          # Muse's models are not in the table


def test_no_model_call_means_no_usage(tmp_path):
    path = _Log().head(str(tmp_path)).prompt("hi").write(tmp_path / "data")
    u = trace.muse_session_usage(path)
    assert u["input_tokens"] is None and u["model"] == "meta/muse-spark-1.2"
    assert accounting.effective(u) == (None, "no_usage")


def test_garbage_usage_values_are_not_counted(tmp_path):
    log = _Log().head(str(tmp_path)).prompt("hi")
    log.run({"kind": "model_completed", "usage": {"input_tokens": "lots", "output_tokens": True,
                                                  "cached_tokens": -5}})
    u = trace.muse_session_usage(log.write(tmp_path / "data"))
    assert u["requests"] == 1 and u["input_tokens"] == 0 and u["output_tokens"] == 0


# ---- finding the log --------------------------------------------------------------------------
def test_the_data_directory_and_the_log_are_found(tmp_path):
    assert trace.muse_data_dir({"TE_MUSE_DATA_DIR": "/a"}) == "/a"
    assert trace.muse_data_dir({"MUSE_PLUGIN_DATA_DIR": "/h/m/plugins/data/tokeneconomizer"}) \
        == "/h/m"
    assert trace.muse_data_dir({"MUSE_PLUGIN_DATA_DIR": "/elsewhere/x", "XDG_DATA_HOME": "/x"}) \
        == os.path.join("/x", "muse")
    assert trace.muse_data_dir({"HOME": "/h"}) == os.path.join("/h", ".local", "share", "muse")
    data = tmp_path / "data"
    path = _session(str(tmp_path)).write(data)
    env = {"TE_MUSE_DATA_DIR": str(data)}
    assert trace.muse_find_log(SID, None, env) == path
    assert trace.muse_find_log(SID, path, {}) == path                # a real path wins
    assert trace.muse_find_log(SID, str(tmp_path / "other.txt"), env) == path
    for bad in ("../../etc", "*", SID + "/../x", "", None, "a" * 36):
        assert trace.muse_find_log(bad, None, env) is None


def test_a_session_id_must_be_a_uuid():
    assert trace.muse_safe_session_id(SID) == SID
    for bad in ("..", "x/y", SID + "\n", "s1", 7, None):
        assert trace.muse_safe_session_id(bad) is None


# ---- the host ---------------------------------------------------------------------------------
def test_tool_events_in_the_guard_s_terms():
    host = hosts.Muse()
    name, args, text, code = host.tool_call({"tool_name": "bash", "tool_input": {
        "command": "pytest -q"}, "tool_response": "1 failed\nexit code: 1"})
    assert (name, args, code) == ("bash", {"command": "pytest -q"}, 1)
    # A success with no exit code in it gets none invented.
    assert host.tool_call({"tool_name": "bash", "tool_input": {"command": "ls"},
                           "tool_response": "a.py"})[3] is None
    name, args, _, _ = host.tool_call({"tool_name": "read_file", "tool_input": {"path": "a.py"},
                                       "tool_response": {"content": "x"}})
    assert name == "Read" and args["file_path"] == "a.py"
    assert host.tool_call({"tool_name": "search", "tool_input": {}})[0] == "Grep"
    assert host.tool_call({"tool_name": "write_file", "tool_input": {"path": "b"}})[:2] == (
        "create", {"path": "b"})
    assert host.tool_call({"tool_name": "edit_file", "tool_input": {"path": "b"}})[0] == "edit"
    name, _, text, code = host.tool_call({"tool_name": "bash", "tool_input": {"command": "x"},
                                          "error": "Exit code 2\nboom", "is_interrupt": False,
                                          "duration_ms": 5})
    assert (name, code) == ("bash", 2) and "boom" in text
    assert host.turn_key({"turn_id": "t"}) == "t" and host.turn_key({}) == ""
    assert denylist.supports("muse") is False


def test_prepare_drops_child_threads_side_calls_and_bad_ids(tmp_path):
    host = hosts.Muse()
    env = {"TOKENECONOMIZER_HOME": str(tmp_path / "s"), "TE_MUSE_DATA_DIR": str(tmp_path)}
    base = {"hook_event_name": "UserPromptSubmit", "session_id": SID, "cwd": str(tmp_path)}
    assert host.prepare(base, env)["session_id"] == SID
    assert host.prepare(base, dict(env, MUSE_SESSION_ID=SID, MUSE_THREAD_ID=_sid(2)))[
        "session_id"] == ""
    assert host.prepare(dict(base, session_id="../x"), env)["session_id"] == ""
    assert host.prepare(dict(base, agent_id="a"), env)["session_id"] == ""
    side = tmp_path / "te-side-abc"
    side.mkdir()
    (side / ".te-sidecall").write_text("")
    assert host.prepare(dict(base, cwd=str(side)), env)["session_id"] == ""
    # The marker alone, in a directory not named as a side call's, is no side call.
    (tmp_path / ".te-sidecall").write_text("")
    assert host.prepare(base, env)["session_id"] == SID


def test_a_tool_event_is_filed_under_the_session_s_directory(tmp_path):
    host = hosts.Muse()
    env = {"TOKENECONOMIZER_HOME": str(tmp_path / "s"), "TE_MUSE_DATA_DIR": str(tmp_path / "d")}
    repo = str(tmp_path / "repo")
    host.prepare({"hook_event_name": "SessionStart", "session_id": SID, "cwd": repo}, env)
    p = host.prepare({"hook_event_name": "PostToolUse", "session_id": SID,
                      "cwd": "/somewhere/else", "tool_name": "bash"}, env)
    assert p["cwd"] == repo
    side = os.path.join(str(tmp_path / "s"), "muse-sessions", f"{SID}.json")
    assert oct(os.stat(side).st_mode & 0o777) == "0o600"
    # The end is filed there too, and the record goes with it.
    assert host.prepare({"hook_event_name": "SessionEnd", "session_id": SID,
                         "cwd": "/x"}, env)["cwd"] == repo
    assert not os.path.exists(side)


def test_prepare_finds_the_log_when_muse_sends_no_path(tmp_path):
    data = tmp_path / "d"
    path = _session(str(tmp_path)).write(data)
    env = {"TOKENECONOMIZER_HOME": str(tmp_path / "s"), "TE_MUSE_DATA_DIR": str(data)}
    p = hosts.Muse().prepare({"hook_event_name": "SessionStart", "session_id": SID,
                              "cwd": str(tmp_path), "transcript_path": None}, env)
    assert p["transcript_path"] == path


def test_render_keeps_to_muse_s_closed_schema_and_its_size_cap():
    host = hosts.Muse()
    out = {"systemMessage": "n" * 3000, "additionalContext": "top-level is rejected",
           "continue": False, "hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                    "additionalContext": "ctx"},
           "_progress": ["x"]}
    r = host.render({"hook_event_name": "PostToolUse"}, out)
    assert set(r) == {"systemMessage", "hookSpecificOutput"}
    assert len(r["systemMessage"]) == host.NOTICE_CAP
    assert r["hookSpecificOutput"] == {"hookEventName": "PostToolUse",
                                       "additionalContext": "ctx"}
    assert host.render({"hook_event_name": "Stop"}, out) == {}
    assert host.render({"hook_event_name": "SessionEnd"}, out) == {}
    # An event that may not carry context keeps only its notice.
    assert set(host.render({"hook_event_name": "PreCompact"}, out)) == {"systemMessage"}
    big = "\n".join(f"- fact {i} é" * 20 for i in range(400))
    r = host.render({"hook_event_name": "SessionStart"},
                    {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                            "additionalContext": big}})
    assert len(json.dumps(r).encode("utf-8")) <= host.OUTPUT_CAP
    assert big.startswith(r["hookSpecificOutput"]["additionalContext"])
    assert r["hookSpecificOutput"]["additionalContext"].endswith("é")     # a whole line


def test_settle_returns_once_the_writer_lease_is_gone(tmp_path):
    path = _session(str(tmp_path)).write(tmp_path / "d")
    lock = os.path.join(os.path.dirname(path), ".session.lock")
    open(lock, "w").close()
    os.utime(path, None)                                  # a log still fresh
    t = time.time()
    hosts.Muse().settle(path, wait_s=0.5)                 # last record is `session.end`
    assert time.time() - t < 0.4
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(_Log().prompt("more").lines[0]) + "\n")
    t = time.time()
    hosts.Muse().settle(path, wait_s=0.5)
    assert time.time() - t >= 0.45                        # waited for it, then gave up
    os.remove(lock)
    t = time.time()
    hosts.Muse().settle(path, wait_s=5)
    assert time.time() - t < 0.4


def test_last_active_falls_back_to_the_session_state():
    assert hosts.Muse().last_active(None, {"updated": 42.0}) == 42.0
    assert hosts.Muse().last_active("/nope", {"started": 7}) == 7.0


def test_detection(tmp_path, monkeypatch):
    tp = os.path.join(str(tmp_path), ".local", "share", "muse", "sessions", "2026", "11", "01",
                      SID, "session.jsonl")
    assert isinstance(hosts.detect({"transcript_path": tp, "turn_id": "t"}, {}), hosts.Muse)
    # Muse stamps `turn_id`, which alone reads as Codex; its plugin hooks name the plugin root.
    assert isinstance(hosts.detect({"turn_id": "t"}, {"MUSE_PLUGIN_ROOT": "/p"}), hosts.Muse)
    assert isinstance(hosts.detect({}, {}, hint="muse"), hosts.Muse)
    # A Claude Code session started from Muse's shell, which carries only MUSE_SESSION_ID.
    claude_tp = os.path.join(str(tmp_path), ".claude", "projects", "x", "s.jsonl")
    assert isinstance(hosts.detect({"transcript_path": claude_tp}, {"MUSE_SESSION_ID": SID}),
                      hosts.ClaudeCode)
    for k in ("TE_HOST", "CLAUDECODE", "COPILOT_CLI", "CURSOR_AGENT", "CURSOR_PROJECT_DIR",
              "GROK_SESSION_ID", "FACTORY_SESSION_ID", "DROID_PLUGIN_ROOT"):
        monkeypatch.delenv(k, raising=False)
    assert hosts.default_host({"MUSE_SESSION_ID": SID}) == "muse"
    assert hosts.HOSTS["muse"]().env_session_id({"MUSE_SESSION_ID": SID}) == SID


# ---- through the hook entry point -------------------------------------------------------------
def test_a_side_call_s_own_session_is_left_alone(tmp_path):
    side = tmp_path / "te-side-1"
    side.mkdir()
    (side / ".te-sidecall").write_text("")
    env = _env(tmp_path / "state", tmp_path / "d")
    for event in ("SessionStart", "UserPromptSubmit", "SessionEnd"):
        assert _call(env, {"hook_event_name": event, "session_id": SID, "cwd": str(side),
                           "turn_id": "t", "source": "startup", "prompt": "x"}) == {}
    assert not os.path.exists(tmp_path / "state" / "repos")


def test_bad_input_always_answers_an_empty_object(tmp_path):
    env = _env(tmp_path / "state", tmp_path / "d")
    for raw in ("", "not json", "[1,2]", "null", json.dumps({"hook_event_name": "Stop"}),
                json.dumps({"session_id": {"a": 1}, "hook_event_name": "SessionStart"}),
                json.dumps({"session_id": SID, "hook_event_name": "PostToolUse",
                            "tool_input": "a string", "tool_response": [1, None, {"x": 2}],
                            "cwd": 5})):
        out = io.StringIO()
        hook.main(["--host", "muse"], io.StringIO(raw), out, env)
        assert json.loads(out.getvalue()) == {} or set(json.loads(out.getvalue())) <= {
            "systemMessage", "hookSpecificOutput"}


# ---- side calls -------------------------------------------------------------------------------
def test_side_calls_are_off_by_default():
    from tokeneconomizer.plugin.cli import _config_view
    from tokeneconomizer.plugin.config import LiveConfig
    cfg = LiveConfig()
    assert cfg.muse_sidecall is False and sidecall.runner_for("muse", cfg) is None
    assert _config_view(cfg)["muse"] == {"state": "off", "muse_sidecall": False}
    assert callable(sidecall.runner_for("muse", LiveConfig(muse_sidecall=True)))
    assert sidecall.call_name("muse") == "muse exec"


@pytest.mark.real_sidecalls
def test_a_side_call_runs_with_no_tool_and_its_prompt_in_a_file(monkeypatch):
    monkeypatch.setattr(sidecall, "program", lambda cli: ["/n/node", f"/n/{cli}.js"])
    seen = []
    prompt = 'Say "hi & exit\n100% of %PATH% $(rm -rf ~)'

    def fake_run(cmd, **kw):
        pf = cmd[cmd.index("--prompt-file") + 1]
        seen.append(dict(kw, cmd=cmd, prompt=open(pf, encoding="utf-8").read(),
                         marker=os.path.isfile(os.path.join(kw["cwd"], ".te-sidecall"))))
        out = "\n".join(json.dumps(x) for x in (
            {"method": "view/item", "params": {"kind": "assistant_message", "text": "draft"}},
            {"kind": "run.usage", "usage": {"input_tokens": 900, "cached_tokens": 100,
                                            "output_tokens": 40, "model": "muse-spark-1.2"}},
            {"kind": "run_terminal", "terminal": "completed", "text": "- use pytest"}))
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(sidecall.subprocess, "run", fake_run)
    text, eff = sidecall.muse_call(prompt, model="muse-spark-1.2")
    assert text == "- use pytest" and eff and eff > 0
    (s,) = seen
    cmd = s["cmd"]
    assert cmd[:3] == ["/n/node", "/n/muse.js", "exec"]
    assert all(prompt not in c for c in cmd)               # not on the command line
    assert s["prompt"] == prompt and s["marker"]
    for flag in ("--preset", "--no-session-log", "--json"):
        assert flag in cmd
    assert cmd[cmd.index("--preset") + 1] == "empty"
    for flag in ("--yolo", "--trust-workspace", "--disable-approval"):
        assert flag not in cmd
    assert cmd[cmd.index("--model") + 1] == "muse-spark-1.2"
    assert s["env"]["TE_SIDECALL"] == "1" and s["stdin"] == subprocess.DEVNULL
    assert os.path.basename(s["cwd"]).startswith("te-side-")
    assert not os.path.exists(s["cwd"])                    # the directory is gone


@pytest.mark.real_sidecalls
def test_a_build_that_refuses_preset_gets_the_fallback_flags(monkeypatch):
    monkeypatch.setattr(sidecall, "program", lambda cli: [cli])
    cmds = []

    def fake_run(cmd, **kw):
        cmds.append(cmd)
        if "--preset" in cmd:
            return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="unknown flag")
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
            {"kind": "run_terminal", "text": "ok"}), stderr="")

    monkeypatch.setattr(sidecall.subprocess, "run", fake_run)
    assert sidecall.muse_call("p")[0] == "ok"
    assert len(cmds) == 2 and "--disable-shell" in cmds[1] and "--disable-write" in cmds[1]


def test_exec_replies_in_either_line_form():
    assert trace.muse_exec_reply("") == ("", None)
    assert trace.muse_exec_reply("garbage\n{\n") == ("", None)
    text, usage = trace.muse_exec_reply(json.dumps(
        {"method": "x", "params": {"kind": "run_terminal", "text": "hi"}}))
    assert text == "hi" and usage is None
    text, usage = trace.muse_exec_reply("\n".join(json.dumps(x) for x in (
        {"kind": "assistant_message_committed", "text": "fallback"},
        {"kind": "run.usage", "models": {"a": {"input_tokens": 10, "output_tokens": 2},
                                         "b": {"inputTokens": 5, "outputTokens": 1}}})))
    assert text == "fallback" and usage["input_tokens"] == 15 and usage["output_tokens"] == 3


# ---- history import and the end to end ------------------------------------------------------
def _ctx(env, repo):
    return LiveContext.build(hosts.Muse(), str(repo), env)


def test_history_import_learns_facts_and_prices_the_sessions(tmp_path):
    state, data = tmp_path / "state", tmp_path / "data"
    repo, other = tmp_path / "repo", tmp_path / "other"
    repo.mkdir()
    other.mkdir()
    (repo / "runtests.py").write_text("print('OK')\n")
    for i in range(5):
        _session(str(repo), sid=_sid(i)).write(data, age_s=7200 + i * 60)
    _session(str(repo), sid=_sid(7)).write(data, age_s=0)            # still being written
    _session(str(other), sid=_sid(8)).write(data)                    # another repository
    child = os.path.join(str(data), "sessions", "2026", "11", "01", _sid(0), "subagent",
                         _sid(9))
    os.makedirs(child)
    with open(os.path.join(child, "session.jsonl"), "w") as fh:
        fh.write("")                                                 # never listed on its own
    env = _env(state, data)
    ctx = _ctx(env, repo)
    plan = history.plan(ctx)
    assert sorted(s["id"] for s in plan["sessions"]) == [_sid(i) for i in range(5)]
    assert plan["skipped"] == {"recent": 1}
    res = history.run(ctx, now=time.time())
    assert res["state"] == "done" and res["imported"] == 5
    assert history.describe(history.summary(ctx), "muse").startswith(
        "read 5 earlier session(s) from Muse Code's history")
    injected = ctx.store.read("injection.json")
    assert injected["sessions"] == 5 and "`python runtests.py" in injected["rendered"]
    rows = [r for r in ctx.store.read_lines("ledger.jsonl") if r.get("kind") == "session"]
    assert len(rows) == 5 and all(r["host"] == "muse" and r["prompts"] == 1 for r in rows)
    assert all(r["effective_tokens"] and r["priced_by"] == "fallback" for r in rows)

    # The next session's start carries the fact, in Muse's schema.
    out = _call(env, {"hook_event_name": "SessionStart", "source": "startup", "session_id":
                      _sid(20), "cwd": str(repo), "transcript_path": None,
                      "model": "unknown", "permission_mode": "default"})
    assert set(out) <= {"systemMessage", "hookSpecificOutput"}
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "`python runtests.py" in out["hookSpecificOutput"]["additionalContext"]


def test_a_live_session_is_folded_inside_its_session_end_hook(tmp_path):
    """Muse stops what a hook leaves running, so the fold happens in the (asynchronous)
    SessionEnd hook -- with no TE_FOLD_SYNC -- and picks up an earlier session left pending."""
    state, data = tmp_path / "state", tmp_path / "data"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = _env(state, data)
    env.pop("TE_FOLD_SYNC")
    for sid in (_sid(1), _sid(2)):
        _session(str(repo), sid=sid).write(data, age_s=0)
        base = {"session_id": sid, "cwd": str(repo), "transcript_path": None}
        _call(env, dict(base, hook_event_name="SessionStart", source="startup"))
        _call(env, dict(base, hook_event_name="UserPromptSubmit", prompt="go", turn_id="t1"))
        _call(env, dict(base, hook_event_name="PostToolUse", turn_id="t1", tool_name="bash",
                        tool_input={"command": "ls"}, tool_response="a.py",
                        cwd=str(tmp_path)))
        _call(env, dict(base, hook_event_name="Stop", turn_id="t1", stop_hook_active=False))
    ctx = _ctx(dict(env, TE_FOLD_SYNC="1"), repo)
    s1 = ctx.store.read(os.path.join("sessions", f"{_sid(1)}.json"))
    s1["ended"] = time.time()                         # ended, but its fold never ran
    ctx.store.write(os.path.join("sessions", f"{_sid(1)}.json"), s1)
    assert _call(env, {"hook_event_name": "SessionEnd", "session_id": _sid(2),
                       "cwd": str(repo), "reason": "exit"}) == {}
    assert set(ctx.store.read("folded.json") or {}) == {_sid(1), _sid(2)}


# ---- packaging --------------------------------------------------------------------------------
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO, "plugin")
EVENTS = {"SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "Stop",
          "SessionEnd"}


def test_the_native_manifest_follows_muse_s_rules():
    m = json.load(open(os.path.join(PLUGIN, ".muse-plugin", "plugin.json"), encoding="utf-8"))
    assert m["schemaVersion"] == 1 and m["compat"]["manifestDir"] == ".muse-plugin"
    hooks = m["capabilities"]["hooks"]
    assert {h["event"] for h in hooks} == EVENTS               # no PreToolUse: never blocks
    allowed = {"id", "event", "command", "timeoutMs", "statusMessage", "async",
               "compatibilityName", "outputCapabilities"}
    sources = []
    for h in hooks:
        assert set(h) <= allowed                               # `matcher` is an error there
        assert h["timeoutMs"] >= 1000
        paths = [a for a in h["command"] if "/" in a]
        assert len(paths) == 1                                  # argv: one packaged file
        assert os.path.isfile(os.path.join(PLUGIN, paths[0]))
        sources.append(paths[0])
    assert len(set(sources)) == len(sources)                  # no duplicate hook source
    end = next(h for h in hooks if h["event"] == "SessionEnd")
    assert end.get("async") is True and end["timeoutMs"] >= 120000
    assert all(not h.get("async") for h in hooks if h["event"] != "SessionEnd")


def test_each_wrapper_answers_through_the_bundle_and_fails_open(tmp_path):
    import shutil
    sh = shutil.which("sh")
    if not sh:
        pytest.skip("no sh")
    out = tmp_path / "market"
    subprocess.run([os.sys.executable, os.path.join(REPO, "scripts", "build_plugin.py"),
                    "--out", str(out)], check=True, capture_output=True)
    bundle = out / "tokeneconomizer"
    entries = [p for p in bundle.rglob("*")]
    assert not any(p.is_symlink() for p in entries)            # Muse refuses any symlink
    assert len(entries) <= 4096
    assert max(len(p.relative_to(bundle).parts) for p in entries) <= 16
    assert (bundle / ".muse-plugin" / "plugin.json").stat().st_size <= 256 * 1024
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home),
           "MUSE_PLUGIN_ROOT": str(bundle), "TOKENECONOMIZER_HOME": str(tmp_path / "te"),
           "TE_MUSE_DATA_DIR": str(tmp_path / "d")}
    payload = json.dumps({"hook_event_name": "SessionEnd", "session_id": SID,
                          "cwd": str(tmp_path), "reason": "exit"})
    for name in ("session-start", "session-end", "stop"):
        r = subprocess.run([sh, str(bundle / "muse" / "hooks" / f"{name}.sh")], input=payload,
                           capture_output=True, text=True, env=env, timeout=60)
        assert r.returncode == 0 and json.loads(r.stdout) == {} and r.stderr == ""
    # No plugin root and no launcher beside it: still a valid, empty answer.
    lone = tmp_path / "lone" / "muse" / "hooks"
    lone.mkdir(parents=True)
    shutil.copy(bundle / "muse" / "hooks" / "stop.sh", lone / "stop.sh")
    r = subprocess.run([sh, str(lone / "stop.sh")], input=payload, capture_output=True,
                       text=True, env={"PATH": os.environ.get("PATH", "")}, timeout=30)
    assert r.returncode == 0 and json.loads(r.stdout) == {}
