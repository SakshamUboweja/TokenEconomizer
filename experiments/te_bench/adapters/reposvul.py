"""ReposVulAdapter: the economizer's domain surface for the ReposVul vulnerability eval.

ReposVul (ICSE'24, github.com/Eshe0922/ReposVul) is a repository-level vulnerability
dataset: each instance is a CVE with a fixing commit, from which the PRE-FIX (vulnerable)
files are reconstructed at their real repo paths. The task is the direct analog of
CredData's: run the coding agent as a security auditor over one instance's files, have it
WRITE a findings JSON of (file, line) locations, and grade line-level F1 against the
fix-removed (vulnerable) lines (the ReposVul analog of SWE-bench `resolved`).

Thin subclass of SwebenchAdapter for the same transfer reason as CredData/WebBench: the
inner coding-agent run is the SAME host agent, so its transcript is byte-compatible with
the vendored `reflect` readers and EVERY reading method is INHERITED verbatim. Only
`problem_statements()` (one fixed vulnerability-audit prompt per instance) is overridden.

The prepared ReposVul data is located by TOKENECONOMIZER_REPOSVUL_ROOT (a placeholder you
set).
"""
import glob
import os

from .swebench import SwebenchAdapter

REPOSVUL_ROOT_ENV = "TOKENECONOMIZER_REPOSVUL_ROOT"

REPOSVUL_TASK_PROMPT = (
    "You are a security auditor scanning a source tree for SOFTWARE VULNERABILITIES "
    "(exploitable security flaws) present in the code. Scan EVERY file in the current "
    "directory tree (the working directory is the root of the code to audit). Look for "
    "vulnerable code of any kind: injection (SQL/command/path), improper authentication or "
    "authorization, missing input validation, unsafe deserialization, buffer/bounds "
    "errors, use of broken/weak cryptography, hardcoded credentials, race conditions, "
    "resource leaks, and similar security defects. For EACH vulnerability, record the file "
    "(path RELATIVE to this directory) and the 1-based line number of the vulnerable line, "
    "and write the findings as a JSON array to a findings file. Do NOT modify any source "
    "file you are auditing; only create the findings file. Missing a real vulnerability and "
    "flagging safe code both hurt the score, which is the F1 of the findings against a "
    "ground-truth label set."
)


class ReposVulAdapter(SwebenchAdapter):
    name = "reposvul"

    def __init__(self):
        """The prepared ReposVul data comes from the TOKENECONOMIZER_REPOSVUL_ROOT env-var
        placeholder; every instance under instances/ is used."""
        self.reposvul_root = os.environ.get(REPOSVUL_ROOT_ENV)

    def instances_dir(self):
        if not self.reposvul_root:
            raise RuntimeError(f"no ReposVul root: set ${REPOSVUL_ROOT_ENV}.")
        return os.path.join(self.reposvul_root, "instances")

    def instance_ids(self):
        return sorted(os.path.basename(p)[:-5]
                      for p in glob.glob(os.path.join(self.instances_dir(), "*.json")))

    def problem_statements(self):
        """{iid: audit_prompt} -- every instance gets the SAME vulnerability-audit prompt."""
        return {iid: REPOSVUL_TASK_PROMPT for iid in self.instance_ids()}
