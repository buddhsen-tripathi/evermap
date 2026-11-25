"""Secret scanning for files under .evermap/ (validation rule V9).

Findings never include the matched text. Pattern scanning is imperfect: a clean
result does not prove that no secret is present.
"""

import math
import re

PATTERNS = [
    ("private key", r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY"),
    ("AWS access key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("GitHub token", r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ("GitLab token", r"\bglpat-[A-Za-z0-9_-]{20,}"),
    ("Slack token", r"\bxox[abeoprs]-[A-Za-z0-9-]{10,}"),
    ("Google API key", r"\bAIza[0-9A-Za-z_-]{30,}"),
    ("API secret or webhook signing key",r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{10,}|\bwhsec_[A-Za-z0-9+/=]{16,}"),
    ("API key", r"\bsk-[A-Za-z0-9_-]{20,}"),
    ("npm token", r"\bnpm_[A-Za-z0-9]{30,}"),
    ("JSON Web Token", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."),
    ("URL with embedded credentials", r"\b[a-z][a-z0-9+.-]*://[^\s/:@\"'<>]*:[^\s/@\"'<>]+@"),
    ("assigned credential",
     r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token)\b[\"']?\s*[:=]\s*[\"']?(?=[^\s]*[0-9])[^\s\"',;)]{6,}"),
]
COMPILED = [(name, re.compile(pattern)) for name, pattern in PATTERNS]
TOKEN = re.compile(r"[A-Za-z0-9+/_=-]{20,}")
HEX = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{32,}(?![0-9A-Za-z])")
ANCHOR = re.compile(r"sha256:[0-9a-f]{16}(?:[0-9a-f]{48})?(?![0-9a-f])")
PLAIN_KEYS = {"hash", "commit", "path", "paths", "id", "target", "parent", "symbol"}


def entropy(text):
    counts = {c: text.count(c) for c in set(text)}
    return -sum(n / len(text) * math.log(n / len(text), 2) for n in counts.values())


def looks_random(token):
    digits = sum(c.isdigit() for c in token)
    return (digits >= 3 and any(c.isupper() for c in token) and any(c.islower() for c in token)
            and entropy(token) >= 3.8)


def scan_text(text):
    """Names of the credential patterns found in text."""
    return [name for name, regex in COMPILED if regex.search(text)]


def scan_entropy(text):
    text = ANCHOR.sub("", text)
    if HEX.search(text):
        return ["long hexadecimal string"]
    for token in TOKEN.findall(text):
        for piece in (token, *token.split("/")):
            if "/" not in piece and len(piece) >= 20 and looks_random(piece):
                return ["high-entropy string"]
    return []


def scan_value(value, path="$", key=None, entropy=True):
    """Yield (json path, finding) for every string inside a JSON value, keys included.
    With entropy=False only the named credential patterns apply (for lists of file paths)."""
    if isinstance(value, dict):
        for name, child in value.items():
            for finding in scan_text(name) + (scan_entropy(name) if entropy else []):
                yield path, finding + " in a field name"
            for found in scan_value(child, "%s.%s" % (path, name), name, entropy):
                yield found
    elif isinstance(value, list):
        for index, child in enumerate(value):
            for found in scan_value(child, "%s[%d]" % (path, index), key, entropy):
                yield found
    elif isinstance(value, str):
        findings = scan_text(value)
        if entropy and key not in PLAIN_KEYS:
            findings = findings + scan_entropy(value)
        for finding in findings:
            yield path, finding
