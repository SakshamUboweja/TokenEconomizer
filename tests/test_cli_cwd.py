import os

import pytest

from tokeneconomizer.plugin import cli


def _refused(argv):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    return exc.value.code


def _no_state(home):
    return not (home / "repos").exists() or not os.listdir(home / "repos")


def test_a_cwd_that_does_not_exist_is_refused_and_writes_no_state(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("TOKENECONOMIZER_HOME", str(home))
    missing = tmp_path / "no-such-dir"
    code = _refused(["--cwd", str(missing), "--host", "claude", "config", "set", "microskills=on"])
    assert code == 2
    assert _no_state(home)


def test_a_file_as_cwd_is_refused_and_writes_no_state(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("TOKENECONOMIZER_HOME", str(home))
    f = tmp_path / "afile.txt"
    f.write_text("x")
    code = _refused(["--cwd", str(f), "--host", "claude", "config", "set", "loopguard=off"])
    assert code == 2
    assert _no_state(home)


@pytest.mark.parametrize("argv", [["fold", "--session", "gone"],
                                  ["judge", "--session", "gone", "--job", "gone"]])
def test_fold_and_judge_still_run_for_a_checkout_that_is_gone(tmp_path, monkeypatch, argv):
    # The plugin spawns these for a session whose checkout may have been removed since.
    monkeypatch.setenv("TOKENECONOMIZER_HOME", str(tmp_path / "home"))
    missing = str(tmp_path / "removed-worktree")
    assert cli.main(["--cwd", missing, "--host", "claude", *argv]) == 0
