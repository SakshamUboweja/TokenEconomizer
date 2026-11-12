# TokenEconomizer on Muse Code (Meta)

Muse Code is Meta's terminal coding agent (the `muse` CLI). Support for it is experimental, and
**it has not been run against a real `muse` binary yet**. The adapter follows Meta's published
documentation: the Muse Code manual and changelog (up to 1.4.14), and the developer guides for
the Muse Code SDK (`@muse-code/sdk` 1.4.15), whose plugin reference was checked against 1.3.0.
Its tests use synthetic sessions and hook payloads built from those documents (see
`tests/test_muse.py` for where each shape comes from). Section [Verification](#verification)
lists what still needs a live run.

## What works, and what does not

| Mechanism | Muse Code | Why |
|---|---|---|
| Micro-skills (repository facts at session start) | yes | `SessionStart` accepts `hookSpecificOutput.additionalContext` |
| History import (learn from earlier sessions) | yes | `<data dir>/sessions/<YYYY>/<MM>/<DD>/<id>/session.jsonl` records the tool calls |
| Session cost / effective tokens | yes, priced as `fallback` | each model call's `model_completed` event carries its usage; Muse's models are not in the price table |
| Loop guard (redirect a failing loop) | rule backstop only | `PostToolUse` and `PostToolUseFailure` accept `additionalContext`; the model judge is a detached job, which Muse would stop, so it is not asked here |
| Repository map (`repomap=on`) | unverified | `UserPromptSubmit` accepts `additionalContext`, but the map is built by a detached refresh Muse may stop; `te --host muse repomap refresh` in a terminal builds it |
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
- **The session log.** A hook's `transcript_path` may be null, so the log is found under Muse's
  data directory: the one the plugin's `MUSE_PLUGIN_DATA_DIR` sits in, else
  `$XDG_DATA_HOME/muse`, else `~/.local/share/muse`.
- **Folding.** Muse stops the processes a plugin hook leaves running once the hook returns
  (changelog 1.4.8). So the plugin does not leave a detached `te fold` behind at the end: the
  `SessionEnd` hook folds the session inside the hook, with any earlier session whose fold was
  cut short, within a time budget. The manifest marks that hook asynchronous (observe-only,
  five-minute timeout); Muse documents asynchronous hooks as running in the background, not
  that it waits for one at exit, so this is unverified. A fold cut short stays pending and is
  done at a later session's end.
- **Other background work.** For the same reason the loop guard asks no model judge on Muse
  (it redirects only at its rule backstop), and the history import is never started from a hook
  even with `history_import=on`: run `te --host muse history import` from a terminal. The fold
  of a session that ended without its `SessionEnd` and the repository map's refresh are still
  started in the background at a session's start, and may be stopped.
- **Subagents and side threads.** A hook whose `MUSE_THREAD_ID` differs from `MUSE_SESSION_ID`
  belongs to a child or side thread and is left alone.

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
keeps the tools of MCP servers you configured: these are why the calls start off. Since the
loop guard asks no judge on Muse, the setting matters only to micro-skills' proposer
(`microskills=on`).

Muse's hooks receive no proxy settings and no provider key, so a call made from a hook uses the
key or browser session Muse has stored. Behind an HTTP proxy, side calls fail; with them off,
`microskills=envfacts` still learns and the loop guard keeps its rule-based backstop.

## Verification

Checked offline (`tests/test_muse.py`, and Muse in `tests/test_live_hooks.py`'s guard test):
the log reader against Meta's record shapes and a third-party 0.2.1 capture; prompt counting;
usage; finding the log; the host's payload handling, closed-schema answers and size cap; the
side-call guard; detection; the history import and a learned fact in the next `SessionStart`
answer; folding inside `SessionEnd`; the manifest against Muse's documented rules; and every
wrapper through a built bundle.

Not yet checked on a real `muse`, and what to look at when it is:

1. `muse plugins validate dist/marketplace/tokeneconomizer` reports the native format, and the
   session's startup line counts six runnable hooks with no rejected answers.
2. Whether `transcript_path` is set in a normal run, and that the log is where the adapter looks.
3. How a failing shell command is reported (`PostToolUse` with the exit code in its text, or
   `PostToolUseFailure`), and the shell tool's argument key.
4. Whether Muse waits for the asynchronous `SessionEnd` hook at exit, and whether a resumed
   session keeps the context its first start added.
5. That `muse exec` accepts the side call's flags and prints its answer as `run_terminal`.
6. Whether the record shapes still hold on the current release (the field names come from Meta's
   example records and a 0.2.1 capture). Meta calls the folder layout an implementation detail.

Compatibility: macOS and Linux (the wrappers are POSIX shell; Windows is not supported).
