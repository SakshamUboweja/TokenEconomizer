# TokenEconomizer on Muse Code (Meta)

Muse Code is Meta's terminal coding agent (the `muse` CLI). Support for it is experimental. The
adapter follows Meta's published documentation (the Muse Code manual and changelog up to 1.4.14,
and the developer guides for the Muse Code SDK, `@muse-code/sdk` 1.4.15) and was checked against
a live Muse Code 1.4.18 on Linux: section [Verification](#verification) says what that run
showed. Its tests use synthetic sessions and hook payloads (see `tests/test_muse.py` for where
each shape comes from).

## What works, and what does not

| Mechanism | Muse Code | Why |
|---|---|---|
| Micro-skills (repository facts at session start) | yes | `SessionStart` accepts `hookSpecificOutput.additionalContext` |
| History import (learn from earlier sessions) | yes | `<data dir>/sessions/<YYYY>/<MM>/<DD>/<id>/session.jsonl` records the tool calls |
| Session cost / effective tokens | yes, priced as `fallback` | each model call's `model_completed` event carries its usage; Muse's models are not in the price table |
| Loop guard (redirect a failing loop) | yes; the model judge only with `muse_sidecall=on` | `PostToolUse` and `PostToolUseFailure` accept `additionalContext`; a failing shell call arrives as `PostToolUseFailure` with its exit code |
| Repository map (`repomap=on`) | yes | `UserPromptSubmit` accepts `additionalContext`; the map is built by a detached refresh, as elsewhere |
| Tool pruning | no | Muse documents no per-repository setting that removes a built-in tool from the model's list; `--disable-*` and `--preset` are launch flags |

## Install

```sh
git clone https://github.com/microsoft/TokenEconomizer.git
cd TokenEconomizer
python scripts/build_plugin.py
muse plugins install dist/marketplace/tokeneconomizer
muse plugins approve tokeneconomizer
```

Then start a new Muse session: approved hooks load in the next session.

- The default bundle is also the Muse package. Muse reads `.muse-plugin/plugin.json` in
  preference to the other agents' manifests beside it, and warns once that there are several.
- Install the **bundle**, not the `plugin/` directory of a checkout: `plugin/lib/tokeneconomizer`
  is a symlink, and Muse refuses to install a package that contains any symlink.
- Muse binds an approval to the exact package it reviewed. After a rebuild, run
  `muse plugins update tokeneconomizer` and `muse plugins approve tokeneconomizer` again.
- To remove it: `muse plugins remove tokeneconomizer`.

Check it in the repository:

```sh
~/.tokeneconomizer/bin/te --host muse status
~/.tokeneconomizer/bin/te --host muse doctor
```

## How it is wired

- **Hooks.** `plugin/.muse-plugin/plugin.json` registers six hooks: `SessionStart`,
  `UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure`, `Stop` and `SessionEnd`. A native
  hook is an argv with no shell, and Muse refuses two hooks with the same script, so each event
  has a short wrapper in `plugin/muse/hooks/` that runs the shared launcher with
  `--host muse` (it falls back to its own place for the plugin root, and answers `{}` if the
  launcher is missing). There is no `PreToolUse` hook: the plugin never blocks a call.
- **Answers.** Muse's answer schema is closed: an unknown key, a top-level `additionalContext`,
  or a key an event does not allow rejects the hook's whole answer, and a hook whose stdout or
  stderr passes 16 KiB is killed. The adapter keeps only `systemMessage` (at most 1000
  characters) and `hookSpecificOutput.{hookEventName, additionalContext}`, cut at a line
  boundary to fit, and the wrappers discard stderr (errors still go to
  `~/.tokeneconomizer/errors.log`). A `systemMessage` that arrives while no turn runs shows
  nothing, so the session-start line ("added N learned fact(s)") is not seen on Muse:
  `te --host muse microskills` shows what was added.
- **The session's directory.** Tool events report the tool's own working directory as `cwd`.
  The adapter keeps the session's directory from its first hook, in
  `~/.tokeneconomizer/muse-sessions/<session id>.json` (removed at the session's end), so a
  call run in another directory is still counted for this repository.
- **The session log.** A hook's `transcript_path` is null (always, on 1.4.18), so the log is
  found by session id under Muse's data directory: the one the plugin's `MUSE_PLUGIN_DATA_DIR`
  sits in, else `$XDG_DATA_HOME/muse`, else `~/.local/share/muse`.
- **Shell results.** A shell call's result is a JSON object (`exit_code`, `terminal_status`,
  `output`, and a `chunk_id` that differs on every call). A call that exits 0 comes as
  `PostToolUse` with it in `tool_response`; any other exit comes as `PostToolUseFailure` with it
  in `error`. The adapter gives the loop guard the output and the exit code, and writes the log's
  shell results the way Claude Code does (`Exit code N` first when the call failed). Other
  tools' failures are text that starts `tool failed: `.
- **Folding.** At `SessionEnd` the plugin starts a detached `te fold`, as on the other agents.
  Muse does not wait for hooks at exit (it gives them about half a second, whether or not they
  are asynchronous), but a detached fold, in a process session of its own, outlives both the
  hook and `muse`; this held in `muse exec` and in the terminal UI on 1.4.18. A session that
  ended without its fold is folded at the next session's start.
- **Muse's own agents.** Muse's reminder agents run plugin hooks too (their `PostToolUse` for
  `submit_reminder_decision`), under a `session_id` of their own, with `MUSE_THREAD_ID` set to it
  and `MUSE_SESSION_ID` to the top-level session's. A hook whose `MUSE_THREAD_ID` differs from
  `MUSE_SESSION_ID` belongs to such an agent, a child or a side thread, and is left alone.
- **Resumed sessions.** `muse resume` sends `SessionStart` with `source: "resume"` and the same
  session id, and the model still has what the first start added, so nothing is injected twice.

## Settings, and the cleared environment

Muse runs a plugin's hooks with almost none of your environment: only `HOME`, `PATH`, `USER`,
`LOGNAME`, the temporary-directory and locale variables, `SHELL`, `TERM`, Muse's own session
variables and the plugin's. So `TOKENECONOMIZER_HOME`, `TE_DISABLE` and every other `TE_*`
variable have no effect in a Muse session. Use the config file instead:

```sh
~/.tokeneconomizer/bin/te --host muse config set microskills=on
~/.tokeneconomizer/bin/te --host muse config show
```

## Import the sessions you already ran

```sh
~/.tokeneconomizer/bin/te --host muse history import --dry-run
~/.tokeneconomizer/bin/te --host muse history import
```

It reads only sessions whose recorded directory is this repository, runs only into a repository
with nothing folded yet, and makes no model call. Child logs (`subagent/`) are counted with the
session they belong to. A forked or side-chat session is a session of its own in Muse's log, so
it is imported as one.

## Model calls

The plugin's own model calls (micro-skills' proposer, the loop guard's judge) are off on Muse
until you run `te --host muse config set muse_sidecall=on`. When on, they run

```
muse exec --json --no-session-log --preset empty --disable-reminders \
          --disable-request-user-input --max-model-steps 1 --prompt-file <file>
```

in a throwaway directory, on your default model (or `muse_paid_model`). `--preset empty` starts
the run with no built-in tool, and the call never passes `--yolo`, `--trust-workspace` or
`--disable-approval`. The prompt goes in a file, not on the command line. If your build refuses
any of these flags, the call answers nothing (errors.log says why); there is no weaker retry.
The flags that leave the run with no tool (`--preset`, `--disable-reminders`,
`--disable-request-user-input`) are documented only in Muse's changelog, and `--preset empty`
keeps the tools of MCP servers you configured: these are why the calls start off. The run prints
its answer as a `run_terminal` record and its usage as `run_usage` (1.4.18).

Muse's hooks receive no proxy settings and no provider key, so a call made from a hook uses the
credentials Muse has stored (`muse login`, or `~/.config/muse/auth.json`). A `META_API_KEY` in
your shell does not reach it: without stored credentials Muse answers "missing meta
credentials", and the plugin pauses its side calls until you run `te --host muse config set
microskills=on` (or `loopguard=on`) again. Behind an HTTP proxy, side calls fail; with them off,
`microskills=envfacts` still learns and the loop guard keeps its rule-based backstop.

## Verification

Checked offline (`tests/test_muse.py`, and Muse in `tests/test_live_hooks.py`'s guard test):
the log reader against Meta's record shapes, a third-party 0.2.1 capture and the 1.4.18 shapes
below; prompt counting; usage; finding the log; the host's payload handling, closed-schema
answers and size cap; the side-call guard; detection; the history import and a learned fact in
the next `SessionStart` answer; folding at the session's end and at the next start; the manifest
against Muse's documented rules; and every wrapper through a built bundle.

Checked live on Muse Code 1.4.18 (Linux, `muse-spark-1.3` models), with hooks that also saved
their payloads:

1. `muse plugins validate` reports `valid tokeneconomizer native hooks=6` (one warning: several
   manifests, `.muse-plugin` picked); `install` and `approve` leave six hooks
   `trusted_enabled`, and every one of them ran and answered `{}` with no rejection.
2. `transcript_path` is null in every hook. The log is at
   `<data dir>/sessions/YYYY/MM/DD/<id>/session.jsonl`, where the adapter finds it.
3. A shell call that exits non-zero is reported as `PostToolUseFailure`, its JSON result in
   `error`; one that exits 0 as `PostToolUse`. The shell tool is `bash` and its argument key is
   `command` (with `description` and `workdir`). A missing file for `read_file` is a
   `PostToolUseFailure` whose `error` starts `tool failed: `. *Fixed*: the exit code was not read
   from the JSON, so the loop guard saw no failure at all.
4. Muse does not wait for the `SessionEnd` hook at exit, asynchronous or not (`hooks_ms` about
   500 in `session.end`); the in-hook fold this adapter used was cut short every time. A
   detached `te fold` survives, so the plugin now folds the way it does elsewhere. A resumed
   session keeps what its first start added (the model recalled it with nothing injected again).
5. `muse exec` accepts every side-call flag, writes no session with `--no-session-log`, and
   prints its answer as a `run_terminal` record. *Fixed*: the answer and its usage are inside
   each line's `payload`, which the reader did not look into, so every side call came back
   empty. Muse's "missing meta credentials" now pauses the side calls.
6. The record shapes the reader uses still hold (`runtime.session.metadata`, `route_facts`,
   `run.model.configured`, and the `started`, `assistant_tool_calls_committed`,
   `tool_result_batch_committed`, `assistant_message_committed`, `model_completed` and
   `terminal` run events), as do `retained_frame` lines. *Fixed*: a shell result in the log is
   the same JSON object, never `tool failed: `, so failed shell calls read as successes.

Still unchecked: macOS; the terminal UI's notice display; a micro-skills proposer call from a
hook with stored Muse credentials.

Compatibility: macOS and Linux (the wrappers are POSIX shell; Windows is not supported).
