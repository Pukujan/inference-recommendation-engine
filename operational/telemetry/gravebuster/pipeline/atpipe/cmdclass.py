"""Coarse command categories for receipt events (no command text is stored, only the category).

Used by the supervisor_polling_loop detector to tell wait/status-poll loops from real work.
Categories: sleep_wait, status_poll, vcs_read, vcs_write, forge_read, forge_write, file_read,
search, build_test, script, other.
"""

import re

_SHELL_WRAP = re.compile(
    r"""(?is)^.*?(?:powershell(?:\.exe)?|pwsh(?:\.exe)?|bash|sh|cmd(?:\.exe)?)["']?\s+(?:-\w+(?:\s+[^-\s]\S*)?\s+)*?(?:-command|-[a-z]*c|/c)\s+(.*)$"""
)

_RULES = (
    ("sleep_wait", r"^(start-sleep|sleep|timeout\s+/t|wait-process|wait-job)\b"),
    (
        "status_poll",
        r"^(gh\s+(run\s+(watch|view|list)|pr\s+checks)|get-process|tasklist|get-job|receive-job|"
        r"test-path|tail\s+-f|get-content\s+.*-(tail|wait)\b|curl\s+.*(health|status))",
    ),
    (
        "vcs_write",
        r"^git\s+(commit|push|add|checkout|switch|merge|rebase|reset|stash|tag|apply|am|cherry-pick)\b",
    ),
    ("vcs_read", r"^git\b"),
    (
        "forge_write",
        r"^gh\s+(pr\s+(create|edit|merge|comment|review)|issue\s+(create|edit|comment|close)|api\s+.*-X\s*(POST|PATCH|PUT|DELETE))",
    ),
    ("forge_read", r"^gh\b"),
    ("search", r"^(rg|grep|select-string|findstr|git\s+grep)\b"),
    (
        "file_read",
        r"^(get-content|cat|type|head|tail|less|more|get-childitem|ls|dir|gci|get-item|resolve-path)\b",
    ),
    (
        "build_test",
        r"^(pnpm|npm|npx|yarn|node|pytest|uv|cargo|dotnet|make|tsc|ruff|mypy|python\s+-m\s+(unittest|pytest))\b",
    ),
    ("script", r"^(python|py|pwsh|powershell|bash|sh|\$|@'|@\")"),
)
_COMPILED = tuple((name, re.compile(rx, re.I)) for name, rx in _RULES)


def inner_command(cmd):
    """Strip a shell wrapper (powershell -Command "...", bash -lc '...') and surrounding quotes."""
    s = (cmd or "").strip()
    m = _SHELL_WRAP.match(s)
    if m:
        s = m.group(1).strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def command_class(cmd):
    if not cmd:
        return None
    s = inner_command(cmd)
    # Leading environment assignments ($env:X='y'; FOO=bar cmd) are skipped.
    s = re.sub(r"^(\$env:\w+\s*=\s*('[^']*'|\"[^\"]*\"|\S+)\s*;\s*)+", "", s, flags=re.I)
    s = re.sub(r"^(\w+=\S+\s+)+", "", s)
    for name, rx in _COMPILED:
        if rx.search(s):
            return name
    return "other"
