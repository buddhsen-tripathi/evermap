"""Read-only Git access. Never runs hooks, fsmonitor, external diff, textconv or filters."""

import hashlib
import os
import re
import subprocess

SAFE_CONFIG = [
    "-c", "core.hooksPath=" + os.devnull,
    "-c", "core.fsmonitor=false",
    "-c", "core.quotePath=false",
    "-c", "core.pager=cat",
    "-c", "diff.autoRefreshIndex=false",  # otherwise git diff rewrites .git/index
]
READ_ONLY = {
    "rev-parse", "diff", "show", "cat-file", "ls-tree", "merge-base", "ls-files", "log",
}
_FILTERS = {}


class GitError(Exception):
    """A Git command failed or the directory is not a repository."""


class UnsafeConfig(GitError):
    """The Git configuration has something that cannot be switched off, so nothing runs."""


def _run(root, args, stdin=b""):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    env.pop("GIT_EXTERNAL_DIFF", None)
    try:
        return subprocess.run(["git"] + SAFE_CONFIG + args, cwd=root, env=env, input=stdin,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise GitError("cannot run git: %s" % exc)


def _no_filters(root):
    """-c options that switch off every configured clean/smudge filter: comparing the
    working tree with a commit would otherwise run the filter's command."""
    if root not in _FILTERS:
        result = _run(root, ["config", "--null", "--name-only", "--get-regexp", r"^filter\..+\."])
        options = []
        for name in sorted({_text(n) for n in result.stdout.split(b"\0") if n}):
            driver = name[len("filter."):name.rindex(".")]
            if "=" in driver or "\n" in driver or "\r" in driver:
                # -c splits at the first "=", so this driver cannot be switched off: run nothing.
                raise UnsafeConfig("filter driver %r cannot be disabled safely (its name contains '=' or a line "
                               "break); refusing to run git. Rename or remove it from the Git config." % driver)
            for key, value in (("clean", ""), ("smudge", ""), ("process", ""), ("required", "false")):
                options += ["-c", "filter.%s.%s=%s" % (driver, key, value)]
        _FILTERS[root] = options
    return _FILTERS[root]


def git(root, *args, **kwargs):
    """Run a read-only Git command and return stdout as bytes."""
    if args[0] not in READ_ONLY:
        raise GitError("refusing to run git %s" % args[0])
    result = _run(root, _no_filters(root) + list(args), kwargs.get("stdin", b""))
    if result.returncode not in kwargs.get("ok", (0,)):
        message = result.stderr.decode("utf-8", "replace").strip()
        raise GitError(message or "git %s failed" % args[0])
    if kwargs.get("code"):
        return result.returncode
    return result.stdout


def _text(data):
    return data.decode("utf-8", "surrogateescape")


def _z(data):
    return [_text(part) for part in data.split(b"\0") if part]


def find_root(start):
    try:
        return os.path.realpath(_text(git(start, "rev-parse", "--show-toplevel")).strip())
    except UnsafeConfig:
        raise
    except (GitError, NotADirectoryError):
        raise GitError("not a Git repository: %s" % start)


def resolve(root, ref):
    """Return the commit SHA for ref."""
    if ref.startswith("-"):
        raise GitError("invalid revision: %s" % ref)
    try:
        out = git(root, "rev-parse", "--verify", "--quiet", "--end-of-options", ref + "^{commit}")
    except GitError:
        raise GitError("unknown revision: %s" % ref)
    return _text(out).strip()


def head(root):
    try:
        return resolve(root, "HEAD")
    except GitError:
        raise GitError("the repository has no commits yet")


def merge_base(root, a, b):
    try:
        return _text(git(root, "merge-base", a, b)).strip()
    except GitError:
        raise GitError("%s and %s have no common ancestor" % (a, b))


def is_ancestor(root, a, b):
    return git(root, "merge-base", "--is-ancestor", a, b, ok=(0, 1), code=True) == 0


def is_model_path(path):
    return path == ".evermap" or path.startswith(".evermap/")


def list_files(root):
    """Tracked and untracked, non-ignored files that exist in the working tree."""
    out = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted({p for p in _z(out) if os.path.lexists(os.path.join(root, p))})


def untracked(root):
    return sorted(_z(git(root, "ls-files", "-z", "--others", "--exclude-standard")))


def _lines_of(data):
    return [line.strip() for line in data.splitlines() if line.strip()]


def _similarity(a, b):
    """Share of non-blank lines two files have in common (multiset)."""
    left, right = _lines_of(a), _lines_of(b)
    if not left or not right:
        return 0.0
    counts = {}
    for line in left:
        counts[line] = counts.get(line, 0) + 1
    common = 0
    for line in right:
        if counts.get(line, 0):
            counts[line] -= 1
            common += 1
    return 2.0 * common / (len(left) + len(right))


def read_worktree(root, path):
    """Bytes of a regular file inside root, or None. Never follows links out of root."""
    root = os.path.realpath(root)
    full = os.path.join(root, *path.split("/"))
    real = os.path.realpath(full)
    if real != root and not real.startswith(root.rstrip(os.sep) + os.sep):
        return None
    if not os.path.isfile(real):
        return None
    with open(real, "rb") as handle:
        return handle.read()


LFS_VERSION = b"version https://git-lfs.github.com/spec/v1"
_LFS_OID = re.compile(rb"\Aoid sha256:([0-9a-f]{64})\Z")
_LFS_SIZE = re.compile(rb"\Asize (0|[1-9][0-9]*)\Z")


def lfs_pointer(data):
    """(sha256 hex, size) when data is a Git LFS pointer file, else None."""
    if len(data) > 1024 or not data.startswith(LFS_VERSION + b"\n"):
        return None
    oid = size = None
    for line in data.split(b"\n")[1:]:
        if _LFS_OID.match(line):
            oid = _LFS_OID.match(line).group(1).decode("ascii")
        elif _LFS_SIZE.match(line):
            size = int(_LFS_SIZE.match(line).group(1))
    return (oid, size) if oid and size is not None else None


def same_content(old, new):
    """True when the committed blob old describes the working-tree bytes new."""
    if old.replace(b"\r\n", b"\n") == new.replace(b"\r\n", b"\n"):
        return True
    pointer = lfs_pointer(old)
    return bool(pointer) and pointer == (hashlib.sha256(new).hexdigest(), len(new))


def changed_files(root, base):
    """Changes from commit base to the working tree, untracked files included.

    Returns a list of {path, status, old_path?} with status added, modified,
    deleted or renamed, sorted by path. Paths under .evermap/ are never listed.
    """
    out = _z(git(root, "diff", "--name-status", "-z", "-M", "--no-ext-diff", "--no-textconv",
                 "--end-of-options", base, "--"))
    changes, index = [], 0
    while index < len(out):
        code = out[index][0]
        if code in "RC":
            old, new = out[index + 1], out[index + 2]
            index += 3
            if code == "R":
                changes.append({"path": new, "status": "renamed", "old_path": old})
            else:
                changes.append({"path": new, "status": "added"})
        else:
            path = out[index + 1]
            index += 2
            status = {"A": "added", "D": "deleted"}.get(code, "modified")
            changes.append({"path": path, "status": status})

    # Without the index refresh, files whose stat data changed are listed too: drop
    # those whose content is the same (line endings aside, as for evidence hashes).
    # With filters off, a checked-out Git LFS file never equals its pointer blob, so
    # compare it with the pointer's sha256 and size instead.
    modified = [c for c in changes if c["status"] == "modified"]
    for change, old in zip(modified, blobs(root, ["%s:%s" % (base, c["path"]) for c in modified])):
        new = read_worktree(root, change["path"])
        if old is not None and new is not None and same_content(old, new):
            changes.remove(change)

    # Git does not pair an unstaged move, so pair deleted files with untracked ones here.
    new_paths = [p for p in untracked(root) if not is_model_path(p)]
    for change in [c for c in changes if c["status"] == "deleted"]:
        if is_model_path(change["path"]) or not new_paths:
            continue
        before = show(root, base, change["path"])
        if before is None:
            continue
        scored = []
        for path in new_paths:
            score = _similarity(before, read_worktree(root, path) or b"")
            if score >= 0.5:
                scored.append((-score, path))
        if scored:
            best = min(scored)[1]
            new_paths.remove(best)
            change.update({"status": "renamed", "old_path": change["path"], "path": best})
    changes += [{"path": p, "status": "added"} for p in new_paths]
    changes = [c for c in changes
               if not (is_model_path(c["path"]) and is_model_path(c.get("old_path", c["path"])))]
    return sorted(changes, key=lambda c: (c["path"], c["status"]))


def is_dirty(root):
    """True when the working tree differs from HEAD outside .evermap/."""
    return bool(changed_files(root, "HEAD"))


def show(root, ref, path):
    """Bytes of path at ref, or None when it does not exist there."""
    try:
        return git(root, "cat-file", "blob", "%s:%s" % (ref, path))
    except GitError:
        return None


def blobs(root, names):
    """Bytes for each object name (such as "<commit>:<path>"), or None when it is not a blob.

    --batch reads one name per line, so a name containing a line break (or a carriage
    return, which Git strips from line ends) is never sent: it gets None.
    """
    sent = [n for n in names if "\n" not in n and "\r" not in n]
    if not sent:
        return [None] * len(names)
    data = git(root, "cat-file", "--batch", stdin="".join(n + "\n" for n in sent).encode("utf-8", "surrogateescape"))
    found, pos = {}, 0
    for name in sent:
        end = data.index(b"\n", pos)
        header = data[pos:end].split(b" ")
        pos = end + 1
        if len(header) == 3 and header[2].isdigit():
            size = int(header[2])
            found[name] = data[pos:pos + size] if header[1] == b"blob" else None
            pos += size + 1
        else:
            found[name] = None  # "<name> missing" or "<name> ambiguous"
    return [found.get(n) for n in names]


def files_at(root, ref, prefix):
    """{path: bytes} for regular files under prefix at ref, in one batch."""
    out = _z(git(root, "ls-tree", "-r", "-z", "--full-tree", ref, "--", prefix))
    entries = []
    for line in out:
        meta, path = line.split("\t", 1)
        mode, kind, sha = meta.split(" ")
        if kind == "blob" and mode in ("100644", "100755"):
            entries.append((path, sha))
    if not entries:
        return {}
    stdin = "".join(sha + "\n" for _, sha in entries).encode("ascii")
    data = git(root, "cat-file", "--batch", stdin=stdin)
    files, pos = {}, 0
    for path, _ in entries:
        end = data.index(b"\n", pos)
        size = int(data[pos:end].split(b" ")[2])
        files[path] = data[end + 1:end + 1 + size]
        pos = end + 1 + size + 1
    return files
