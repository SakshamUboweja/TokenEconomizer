"""Muse Code (Meta's terminal coding agent, the `muse` CLI): a session stored as an append-only
event log, `<data dir>/sessions/<YYYY>/<MM>/<DD>/<session id>/session.jsonl`.

The data directory is `$XDG_DATA_HOME/muse`, by default `~/.local/share/muse`. Muse calls the
folder layout "implementation detail" and documents no stability promise for the record shapes,
so everything here is defensive: an unknown line, record or event kind is skipped, never an
error, and a session that yields nothing is read as an empty one.

Each line is an envelope:

    {"schema_version": 1, "id", "stream": {"kind": "session", "id"}, "sequence",
     "recorded_at" (microseconds), "record_type": "event", "durability", "causation_id",
     "payload_type", "payload_schema_version", "payload"}

or a `{"retained_frame": ..., "children": [{"record_json": "<envelope as a JSON string>"}]}`
wrapper around envelopes, or a `{"retained_marker": ...}` stand-in with no content (skipped).
The records read here:

  * `runtime.session.metadata`     -- `payload.record.{workspace_root, model_id}`;
  * `runtime.session.route_facts`  -- `payload.record.cwd`, the session's directory;
  * `run.model.configured`         -- `payload.record.model_id`, a fallback for the model;
  * `runtime.session` with `payload.kind == "run"`, by `payload.event.kind`:
      - `started` with a non-empty `prompt`: a prompt (a task's `started` carries none);
      - `assistant_tool_calls_committed`: `tool_calls[].{call_id, name, args}`, `args` a JSON
        string;
      - `tool_result_batch_committed`: `results[].{tool_call_id, text}`; a failure is text that
        starts `tool failed: ` (there is no error flag);
      - `assistant_message_committed`: `text`;
      - `model_completed`: `model`, `usage{input_tokens, output_tokens, cached_tokens,
        cache_read_tokens, cache_write_tokens, reasoning_tokens}`, one per model call;
      - `terminal`: `terminal` (`completed`, `failed`, `cancelled`), the end of a turn;
  * `session.end`                  -- one per process exit (a resumed log has several).

`muse_session_to_claude` writes a session as Claude Code transcript lines, which the shared
readers (`load_trajectory`, `claude_calls`) take from there.
"""
import glob
import json
import os
import re

SYSTEM_PREFIXES = ("<system-reminder", "<local-command", "[Request interrupted")
FAILED_PREFIX = "tool failed: "
INTERRUPTED = "[Request interrupted by user]"

# Muse's built-in tool names (its import guide: Write -> write_file, Edit -> edit_file, Read ->
# read_file, Bash -> bash, Grep -> search, WebFetch -> web_fetch, WebSearch -> web_search; the
# guard example also names `powershell`) as the Claude Code names the mechanisms key on. Any
# other name -- `mcp__*`, `read_skill`, `request_user_input` -- is kept as it is.
_TOOL_NAMES = {
    "bash": "Bash", "powershell": "Bash", "shell": "Bash",
    "read_file": "Read", "write_file": "Write", "edit_file": "Edit",
    "search": "Grep", "web_fetch": "WebFetch", "web_search": "WebSearch",
}
_PATH_TOOLS = ("Read", "Write", "Edit")

# Muse's session ids are UUIDs, and an id names files and directories here and in the plugin's
# state, so nothing else is taken.
SESSION_ID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                           r"[0-9a-fA-F]{12}")
# A line longer than this is not a record the readers use (a tool's whole output is kept in
# `tool-outputs/`, not inline); it is skipped rather than parsed.
MAX_LINE = 8 * 1024 * 1024


def claude_tool_name(name):
    """A Muse tool name as the equivalent Claude Code one, or unchanged."""
    name = name if isinstance(name, str) else ""
    return _TOOL_NAMES.get(name.lower(), name)


def muse_safe_session_id(sid):
    """`sid` when it is a UUID (safe as a file name), else None."""
    return sid if isinstance(sid, str) and SESSION_ID_RE.fullmatch(sid) else None


def muse_data_dir(env=None):
    """Where Muse keeps its sessions: `TE_MUSE_DATA_DIR` (tests, a non-default install), else
    the data directory a plugin hook's `MUSE_PLUGIN_DATA_DIR` sits in
    (`<data dir>/plugins/data/<plugin id>`), else `$XDG_DATA_HOME/muse`, else
    `~/.local/share/muse`."""
    env = os.environ if env is None else env
    if env.get("TE_MUSE_DATA_DIR"):
        return env["TE_MUSE_DATA_DIR"]
    pdata = env.get("MUSE_PLUGIN_DATA_DIR")
    if pdata:
        parts = os.path.normpath(pdata)
        parent = os.path.dirname(parts)                 # <data>/plugins/data
        if os.path.basename(parent) == "data" and \
                os.path.basename(os.path.dirname(parent)) == "plugins":
            return os.path.dirname(os.path.dirname(parent))
    if env.get("XDG_DATA_HOME"):
        return os.path.join(env["XDG_DATA_HOME"], "muse")
    home = env.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".local", "share", "muse")


def _envelopes(obj):
    """The envelopes one parsed line holds: itself, a retained frame's children, or none."""
    if not isinstance(obj, dict):
        return []
    if "retained_marker" in obj:
        return []
    if "retained_frame" in obj:
        out = []
        for child in obj.get("children") or []:
            raw = child.get("record_json") if isinstance(child, dict) else None
            if not isinstance(raw, str):
                continue
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if isinstance(ev, dict) and "payload" in ev:
                out.append(ev)
        return out
    return [obj] if "payload" in obj else []


def muse_records(path):
    """The envelopes of a Muse session log, in file order; [] if it cannot be read. A truncated
    last line (a session still being written) is skipped."""
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if len(line) > MAX_LINE:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                out.extend(_envelopes(obj))
    except (OSError, TypeError, ValueError):
        return []
    return out


def _payload(rec):
    p = rec.get("payload")
    return p if isinstance(p, dict) else {}


def _run_event(rec):
    """The `event` of a conversation record (`runtime.session`, kind `run`), else None."""
    if rec.get("payload_type") != "runtime.session":
        return None
    p = _payload(rec)
    if p.get("kind") != "run":
        return None
    ev = p.get("event")
    return ev if isinstance(ev, dict) else None


def _record(rec, payload_type):
    if rec.get("payload_type") != payload_type:
        return None
    r = _payload(rec).get("record")
    return r if isinstance(r, dict) else None


def _str(v):
    return v if isinstance(v, str) and v else None


def muse_session_info(path, records=None):
    """`{cwd, workspace_root, model, started}` from a session's metadata records; each None
    where the log does not say. `cwd` is the route facts' directory, else the workspace root;
    `started` is the first record's time in epoch seconds."""
    records = muse_records(path) if records is None else records
    out = {"cwd": None, "workspace_root": None, "model": None, "started": None}
    for rec in records:
        if out["started"] is None:
            at = rec.get("recorded_at")
            if isinstance(at, (int, float)) and not isinstance(at, bool) and at > 0:
                out["started"] = at / 1e6
        meta = _record(rec, "runtime.session.metadata")
        if meta:
            out["workspace_root"] = out["workspace_root"] or _str(meta.get("workspace_root"))
            out["model"] = out["model"] or _str(meta.get("model_id"))
        route = _record(rec, "runtime.session.route_facts")
        if route and not out["cwd"]:
            out["cwd"] = _str(route.get("cwd"))
        if out["cwd"] and out["workspace_root"] and out["model"] and out["started"]:
            break
    out["cwd"] = out["cwd"] or out["workspace_root"]
    return out


def muse_log_head(path):
    """A session's `{cwd, started, entrypoint}` for the history import ({} if unreadable).
    Muse records no front end, so `entrypoint` is always None."""
    info = muse_session_info(path)
    out = {"entrypoint": None}
    if info["cwd"]:
        out["cwd"] = info["cwd"]
    if info["started"]:
        out["started"] = info["started"]
    return out


def _prompt(ev):
    """The prompt a `started` event carries, when it is one the person typed, else None."""
    if ev.get("kind") != "started":
        return None
    text = ev.get("prompt")
    if not isinstance(text, str) or not text.strip():
        return None
    if text.lstrip().startswith(SYSTEM_PREFIXES):
        return None
    return text


def muse_prompt_count(path):
    """How many prompts the session ran: `started` run events with a prompt. A task's
    `started` (kind `task`) carries none and is not counted; neither is injected text."""
    n = 0
    for rec in muse_records(path):
        ev = _run_event(rec)
        if ev is not None and _prompt(ev) is not None:
            n += 1
    return n


def _args(raw):
    """A tool call's `args` (a JSON string) as a dict; `{"raw": ...}` when it is not JSON."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            val = json.loads(raw)
        except ValueError:
            return {"raw": raw}
        return val if isinstance(val, dict) else {"raw": raw}
    return {}


def claude_tool_input(native, args):
    """A Muse tool call's input in the keys the Claude Code readers use: `path` is also given
    as `file_path` for the file tools; the shell's command as `command`."""
    name = claude_tool_name(native)
    args = dict(args) if isinstance(args, dict) else {}
    if name in _PATH_TOOLS and "file_path" not in args:
        path = args.get("path") or args.get("file")
        if isinstance(path, str):
            args["file_path"] = path
    if name == "Bash" and not isinstance(args.get("command"), str):
        for key in ("cmd", "script", "raw"):
            if isinstance(args.get(key), str):
                args["command"] = args[key]
                break
    return args


def _claude_lines(records, cwd):
    """(Claude line, is_prompt) for each conversation event of a session, in order."""
    model = None
    for rec in records:
        if rec.get("payload_type") == "run.model.configured":
            r = _record(rec, "run.model.configured")
            model = _str((r or {}).get("model_id")) or model
            continue
        ev = _run_event(rec)
        if ev is None:
            continue
        kind = ev.get("kind")
        line = None
        prompt = False
        if kind == "started":
            text = _prompt(ev)
            if text is not None:
                line = {"type": "user", "message": {"role": "user", "content": [
                    {"type": "text", "text": text}]}}
                prompt = True
        elif kind == "model_completed":
            model = _str(ev.get("model")) or model
        elif kind == "assistant_tool_calls_committed":
            blocks = []
            for call in ev.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                cid = _str(call.get("call_id")) or _str(call.get("id"))
                if not cid:
                    continue
                native = call.get("name") if isinstance(call.get("name"), str) else ""
                blocks.append({"type": "tool_use", "id": cid, "name": claude_tool_name(native),
                               "input": claude_tool_input(native, _args(call.get("args")))})
            if blocks:
                line = {"type": "assistant", "message": {"role": "assistant",
                                                          "content": blocks}}
        elif kind == "tool_result_batch_committed":
            blocks = []
            for res in ev.get("results") or []:
                if not isinstance(res, dict) or not _str(res.get("tool_call_id")):
                    continue
                text = res.get("text") if isinstance(res.get("text"), str) else ""
                blocks.append({"type": "tool_result", "tool_use_id": res["tool_call_id"],
                               "content": text, "is_error": text.startswith(FAILED_PREFIX)})
            if blocks:
                line = {"type": "user", "message": {"role": "user", "content": blocks}}
        elif kind == "assistant_message_committed":
            text = ev.get("text")
            if isinstance(text, str) and text.strip():
                line = {"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "text", "text": text}]}}
        elif kind == "terminal" and ev.get("terminal") == "cancelled":
            line = {"type": "user", "message": {"role": "user", "content": [
                {"type": "text", "text": INTERRUPTED}]}}
        if line is None:
            continue
        if line["type"] == "assistant" and model:
            line["message"]["model"] = model
        if cwd:
            line["cwd"] = cwd
        yield line, prompt


def muse_session_to_claude(path, out_path):
    """Write a Muse session as Claude Code transcript lines, for `load_trajectory`.

    Every line carries the session's directory (Muse's log records none per call). Returns the
    number of tool calls written, or None when the log is missing."""
    if not path or not os.path.isfile(path):
        return None
    records = muse_records(path)
    cwd = muse_session_info(path, records)["cwd"]
    n = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for line, _ in _claude_lines(records, cwd):
            n += sum(1 for b in line["message"]["content"] if b.get("type") == "tool_use")
            out.write(json.dumps(line) + "\n")
    return n


def muse_prompt_turns(path):
    """Each prompt-turn of a Muse session, as Claude Code log lines from its prompt to the next,
    one turn at a time (the history import seeds the loop guard's envelope from them)."""
    records = muse_records(path)
    cwd = muse_session_info(path, records)["cwd"]
    current = None
    for line, prompt in _claude_lines(records, cwd):
        text = json.dumps(line)
        if prompt:
            if current is not None:
                yield current
            current = [text]
        elif current is not None:
            current.append(text)
    if current is not None:
        yield current


def _count(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0


def muse_session_usage(path):
    """Token breakdown of a Muse session: the sum of its `model_completed` events, its child
    logs' (`subagent/*/session.jsonl`) included.

    Cached input is read as part of `input_tokens` (Meta's models; Muse's schema calls it
    "provider-convention-dependent") and taken out of it, and reasoning as part of
    `output_tokens`. Without a `model_completed` event every count is None, so the ledger says
    `no_usage` rather than guessing."""
    out = {"input_tokens": None, "cache_read_input_tokens": None,
           "cache_creation_input_tokens": None, "output_tokens": None,
           "requests": 0, "model": None}
    if not path:
        return out
    logs = [path]
    root = os.path.dirname(path)
    logs += sorted(glob.glob(os.path.join(glob.escape(root), "subagent", "*", "session.jsonl")))
    totals = {"input_tokens": 0, "cache_read_input_tokens": 0,
              "cache_creation_input_tokens": 0, "output_tokens": 0}
    models, requests, meta_model = {}, 0, None
    for log in logs:
        records = muse_records(log)
        if log == path:
            meta_model = muse_session_info(log, records)["model"]
        for rec in records:
            ev = _run_event(rec)
            if ev is None or ev.get("kind") != "model_completed":
                continue
            usage = ev.get("usage") if isinstance(ev.get("usage"), dict) else {}
            requests += 1
            cached = max(_count(usage.get("cache_read_tokens")), _count(usage.get("cached_tokens")))
            totals["input_tokens"] += max(0, _count(usage.get("input_tokens")) - cached)
            totals["cache_read_input_tokens"] += cached
            totals["cache_creation_input_tokens"] += _count(usage.get("cache_write_tokens"))
            totals["output_tokens"] += _count(usage.get("output_tokens"))
            m = _str(ev.get("model"))
            if m:
                models[m] = models.get(m, 0) + 1
    out["requests"] = requests
    out["model"] = max(models, key=models.get) if models else meta_model
    if requests:
        out.update(totals)
    return out


def muse_find_log(session_id, transcript_path=None, env=None):
    """The log of session `session_id`: `transcript_path` when it is an existing
    `session.jsonl`, else the one under the data directory's date tree; None if neither."""
    if isinstance(transcript_path, str) and transcript_path and \
            os.path.basename(transcript_path) == "session.jsonl" and \
            os.path.isfile(transcript_path):
        return transcript_path
    sid = muse_safe_session_id(session_id)
    if sid is None:
        return None
    root = os.path.join(muse_data_dir(env), "sessions")
    hits = glob.glob(os.path.join(glob.escape(root), "*", "*", "*", sid, "session.jsonl"))
    return sorted(hits)[-1] if hits else None


def muse_sessions(root=None, env=None):
    """Every top-level session log under Muse's data directory, oldest mtime first. Child logs
    (`<session>/subagent/<id>/session.jsonl`) are part of their session and never listed.

    Each row is `{path, id, cwd, started, mtime, size, folder, parent}`; `folder` and `parent`
    are None (Muse files sessions by date, and a fork is a session of its own)."""
    base = os.path.join(root or muse_data_dir(env), "sessions")
    rows = []
    for path in glob.glob(os.path.join(glob.escape(base), "[0-9]*", "[0-9]*", "[0-9]*", "*",
                                       "session.jsonl")):
        sid = os.path.basename(os.path.dirname(path))
        if muse_safe_session_id(sid) is None:
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        head = muse_log_head(path)
        rows.append({"path": path, "id": sid, "cwd": head.get("cwd"),
                     "started": head.get("started") or st.st_mtime,
                     "mtime": st.st_mtime, "size": st.st_size, "folder": None, "parent": None})
    rows.sort(key=lambda r: (r["mtime"], r["path"]))
    return rows


def muse_ended_since(path, since):
    """Has the log a `session.end` record written at or after `since` (epoch seconds)?"""
    for rec in reversed(muse_records(path)):
        if rec.get("payload_type") != "session.end":
            continue
        at = rec.get("recorded_at")
        if isinstance(at, (int, float)) and not isinstance(at, bool) and at / 1e6 >= since:
            return True
    return False


def _usage_from(d):
    """A `run.usage` / token-usage object (snake or camel case) as the four counts, or None."""
    if not isinstance(d, dict):
        return None

    def get(*keys):
        for k in keys:
            v = d.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return int(v)
        return None
    inp = get("input_tokens", "inputTokens", "prompt_tokens", "promptTokens")
    if inp is None:
        return None
    cached = max(get("cache_read_tokens", "cacheReadTokens") or 0,
                 get("cached_tokens", "cachedTokens") or 0)
    return {"input_tokens": max(0, inp - cached), "cache_read_input_tokens": cached,
            "cache_creation_input_tokens": get("cache_write_tokens", "cacheWriteTokens") or 0,
            "output_tokens": get("output_tokens", "outputTokens") or 0,
            "model": d.get("model") if isinstance(d.get("model"), str) else None}


def muse_exec_reply(stdout):
    """`(text, usage)` from `muse exec --json` output: the answer from its `run_terminal` line
    (else the last assistant text it printed), the usage from a `run.usage` line, if any. Lines
    come either flat or as MSP view notifications (`{"method", "params"}`); both are read."""
    text, last, usage = "", "", None
    for raw in (stdout or "").splitlines():
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        items = [obj]
        if isinstance(obj.get("params"), dict):
            items.append(obj["params"])
        for it in items:
            kind = str(it.get("kind") or it.get("type") or it.get("method") or "")
            if kind == "run_terminal" and isinstance(it.get("text"), str) and it["text"].strip():
                text = it["text"]
            elif kind in ("run.usage", "usage") or "usage" in kind.lower():
                u = it.get("usage") if isinstance(it.get("usage"), dict) else it
                models = it.get("models") if isinstance(it.get("models"), (list, dict)) else None
                if models:
                    vals = models.values() if isinstance(models, dict) else models
                    parts = [p for p in (_usage_from(v) for v in vals) if p]
                    if parts:
                        u = None
                        usage = {k: sum(p[k] for p in parts) for k in (
                            "input_tokens", "cache_read_input_tokens",
                            "cache_creation_input_tokens", "output_tokens")}
                        usage["model"] = next((p["model"] for p in parts if p["model"]), None)
                if u is not None:
                    usage = _usage_from(u) or usage
            elif kind in ("assistant_message_committed", "assistant_message") and \
                    isinstance(it.get("text"), str) and it["text"].strip():
                last = it["text"]
            ev = it.get("event")
            if isinstance(ev, dict) and ev.get("kind") == "assistant_message_committed" and \
                    isinstance(ev.get("text"), str) and ev["text"].strip():
                last = ev["text"]
    return (text or last), usage
