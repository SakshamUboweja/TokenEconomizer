"""CredDataAdapter: the economizer's domain surface for the CredData secret-finding eval.

CredData (Samsung, github.com/Samsung/CredData) is 333 repos of obfuscated source, each
shipped as `data/<repoid>/` with a per-repo label CSV `meta/<repoid>.csv`. The task: run
the coding agent as a security auditor over a disposable copy of one repo, have it WRITE a
findings JSON, and grade line-level detection F1 against the labels (that F1 is the
CredData analog of SWE-bench `resolved` -- the HARD quality guardrail).

The inner coding-agent run is the SAME host agent SWE-bench's runner drives, so the
`transcript.jsonl` is byte-compatible with the vendored `reflect` readers; EVERY
trajectory / tool-usage / catalog / waste / spiral-envelope method is INHERITED verbatim.
Only one domain-specific surface is overridden:

  problem_statements()  -> {repoid: fixed security-audit prompt}. The CredData task is the
                          SAME audit instruction for every repo, so every repoid maps to
                          the one prompt (what the untool per-task judge keys on).

The CredData checkout is located by TOKENECONOMIZER_CREDDATA_ROOT (a placeholder you set).
The task EXECUTOR + GRADER stay ABOVE the economizer at
the runner layer, exactly as the SWE-bench Docker grade does.
"""
import glob
import os

from .swebench import SwebenchAdapter

CREDDATA_ROOT_ENV = "TOKENECONOMIZER_CREDDATA_ROOT"

CREDDATA_TASK_PROMPT = (
    "You are a security auditor scanning a source tree for HARDCODED CREDENTIALS "
    "(secrets) that have been leaked into the code. Scan EVERY file in the current "
    "directory tree (the working directory is the root of the code to audit). Look for "
    "hardcoded credentials of any kind: passwords, API keys, secret keys, tokens, private "
    "keys, nonces, salts, seeds, AWS keys, credentials embedded in URLs, and similar "
    "secret material. For EACH secret, record the file (path RELATIVE to this directory) "
    "and the 1-based line number where the secret value appears, and write the findings as "
    "a JSON array to a findings file. Do NOT modify any source file you are auditing; only "
    "create the findings file. Missing a real secret and flagging a non-secret both hurt "
    "the score, which is the F1 of the findings against a ground-truth label set."
)


class CredDataAdapter(SwebenchAdapter):
    name = "creddata"

    def __init__(self):
        """The CredData checkout comes from the TOKENECONOMIZER_CREDDATA_ROOT env-var
        placeholder; every repo under meta/ is enumerated."""
        self.creddata_root = os.environ.get(CREDDATA_ROOT_ENV)

    def meta_dir(self):
        if not self.creddata_root:
            raise RuntimeError(f"no CredData root: set ${CREDDATA_ROOT_ENV}.")
        return os.path.join(self.creddata_root, "meta")

    def repo_ids(self):
        return sorted(os.path.basename(p)[:-4]
                      for p in glob.glob(os.path.join(self.meta_dir(), "*.csv")))

    def problem_statements(self):
        """{repoid: audit_prompt} -- every CredData repo gets the SAME audit instruction."""
        return {rid: CREDDATA_TASK_PROMPT for rid in self.repo_ids()}
