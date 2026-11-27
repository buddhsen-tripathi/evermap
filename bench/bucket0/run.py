"""Run one Evermap first-map benchmark on a fresh clone of bucket0 (BENCHMARK §2.3).

Follows the skill's own procedure: discover.md "Calling agent" and headless.md. One headless
worker per packet, a continuation run for packets that leave files unowned, then the final
pass, sync and render. Logs the cost, tokens, turns and duration of every worker. No cost cap:
the runs use existing subscriptions, so the cost is logged, not limited.

Usage: python3 run.py --agent claude|codex --run NAME [--source URL|DIR] [--commit SHA] [--model sonnet]
Clones the public repository at the pinned commit unless told otherwise, and uses the skill in
this checkout. Writes raw/<NAME>/ (worker outputs, gitignored) and results/<NAME>.json (numbers).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))  # this Evermap checkout
SKILL = os.environ.get("EVERMAP_SKILL", os.path.join(ROOT, "skills", "evermap"))
SOURCE = "https://github.com/buddhsen-tripathi/bucket0.git"
COMMIT = "26387d87e6fa958a82a9ed75674bfcc873a562e5"  # the commit every published result maps
SCRIPT = os.path.join(SKILL, "scripts", "evermap.py")
WORKDIR = os.environ.get("EVERMAP_BENCH_WORK", os.path.join(tempfile.gettempdir(), "evermap-bench"))

SYSTEM = {
    "name": "Bucket0",
    "summary": "Encrypted cloud storage on Cloudflare Workers: React Router and Hono with Postgres "
               "(Drizzle), R2, a background queue and a media container.",
    "plain": "Bucket0 lets people store files encrypted in the cloud, share them, work on them with "
             "a team, and reach their storage from S3 tools or AI agents.",
}
SCOPE = ["server/**", "lib/**", "workers/**", "media/**", "db/**", "wrangler.jsonc", "vite.config.ts",
         "web/root.tsx", "web/routes.ts"]
EXCLUDE = ["**/node_modules/**", "**/dist/**", "**/build/**", "docs/**", "**/*.md"]
KINDS, DIAGRAMS = "sequence,er,state,access", "excalidraw,eraser"


def fenced(text, after):
    """First fenced block after the heading or phrase `after`."""
    part = text.split(after, 1)[1]
    return part.split("```", 2)[1].split("\n", 1)[1].rstrip("\n")


def guides():
    read = lambda n: open(os.path.join(SKILL, "references", n), encoding="utf-8").read()
    headless, discover = read("headless.md"), read("discover.md")
    packet = fenced(headless, "## Task packet")
    tail = packet[packet.index("For stale evidence"):]
    return {
        "packet": packet,
        "continuation": fenced(headless, "Report\n  the files still unowned"),
        "final": fenced(discover, "## Calling agent") + "\n" + tail,
    }


def fill(text, n=None):
    text = text.replace("<absolute skill dir>", SKILL)
    return text.replace("<n>", str(n)) if n is not None else text


def evermap(work, *args):
    out = subprocess.run([sys.executable, "-B", SCRIPT] + list(args) + ["--format", "json", "--max-chars", "0", "--limit", "0"],
                         cwd=work, capture_output=True, text=True)
    try:
        return json.loads(out.stdout)
    except ValueError:
        return {"_exit": out.returncode, "_stderr": out.stderr[-500:]}


def worker(agent, model, work, raw, label, text):
    os.makedirs(raw, exist_ok=True)
    with open(os.path.join(raw, label + ".packet.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    start = time.time()
    rec = {"label": label}
    if agent == "claude":
        cmd = ["claude", "-p", text, "--model", model, "--strict-mcp-config", "--disable-slash-commands",
               "--tools", "Read,Grep,Glob,Edit,Write,Bash",
               "--allowedTools", "Read(/%s/**)" % SKILL, "Edit(.evermap/**)", "Write(.evermap/**)",
               "Bash(python3 %s:*)" % SCRIPT,
               "--permission-mode", "default", "--no-session-persistence", "--output-format", "json"]
        out = os.path.join(raw, label + ".json")
        with open(out, "w") as so, open(os.path.join(raw, label + ".err"), "w") as se:
            rec["exit"] = subprocess.run(cmd, cwd=work, stdin=subprocess.DEVNULL, stdout=so, stderr=se).returncode
        try:
            r = json.load(open(out))
            usage = r.get("usage") or {}
            rec.update({"subtype": r.get("subtype"), "is_error": r.get("is_error"),
                        "cost_usd": r.get("total_cost_usd"), "turns": r.get("num_turns"),
                        "input_tokens": usage.get("input_tokens"),
                        "cache_read_tokens": usage.get("cache_read_input_tokens"),
                        "cache_write_tokens": usage.get("cache_creation_input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "reply": str(r.get("result", ""))})
        except ValueError:
            rec["reply"] = ""
    else:
        txt, log = os.path.join(raw, label + ".txt"), os.path.join(raw, label + ".log")
        cmd = ["codex", "exec", "--ephemeral", "--sandbox", "workspace-write", "-o", txt, text]
        with open(log, "w") as so:
            rec["exit"] = subprocess.run(cmd, cwd=work, stdin=subprocess.DEVNULL, stdout=so,
                                         stderr=subprocess.STDOUT).returncode
        logtext = open(log, encoding="utf-8", errors="replace").read()
        used = re.findall(r"tokens used\s*\n?\s*([\d,]+)", logtext)
        rec["tokens_used"] = int(used[-1].replace(",", "")) if used else None
        model_line = re.search(r"^model:\s*(.+)$", logtext, re.M)
        rec["model"] = model_line.group(1).strip() if model_line else None
        rec["reply"] = open(txt, encoding="utf-8").read() if os.path.exists(txt) else ""
    rec["seconds"] = round(time.time() - start, 1)
    return rec


def check(work, head, rec):
    st = evermap(work, "status")
    rec["head_unchanged"] = st.get("head") == head
    rec["dirty"] = st.get("working_tree_dirty")
    rec["draft_left"] = os.path.exists(os.path.join(work, ".evermap", "draft.json"))
    v = evermap(work, "validate")
    rec["errors"] = v.get("errors")
    rec["warnings"] = v.get("warnings")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=("claude", "codex"), required=True)
    ap.add_argument("--run", required=True)
    ap.add_argument("--source", default=SOURCE, help="URL or directory to clone (default: the public repository)")
    ap.add_argument("--commit", default=COMMIT)
    ap.add_argument("--model", default="sonnet")
    args = ap.parse_args()
    work, raw = os.path.join(WORKDIR, args.run), os.path.join(HERE, "raw", args.run)
    if os.path.exists(work):
        sys.exit("%s exists; pick another --run" % work)
    os.makedirs(WORKDIR, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", args.source, work], check=True)
    subprocess.run(["git", "checkout", "-q", args.commit], cwd=work, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=work, capture_output=True, text=True).stdout.strip()
    skill_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=SKILL, capture_output=True, text=True).stdout.strip()
    g = guides()

    init = ["init", "--name", SYSTEM["name"], "--summary", SYSTEM["summary"], "--plain", SYSTEM["plain"],
            "--kinds", KINDS, "--diagrams", DIAGRAMS]
    for s in SCOPE:
        init += ["--scope", s]
    for e in EXCLUDE:
        init += ["--exclude", e]
    evermap(work, *init)
    est = evermap(work, "estimate", "--write-plan")
    packets = est.get("total_packets") or 0
    log = {"run": args.run, "agent": args.agent, "source": args.source if "://" in args.source else "local clone", "model": args.model if args.agent == "claude" else None,
           "repo_commit": head, "skill_commit": skill_commit, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "estimate": {k: est.get(k) for k in ("files", "code_tokens", "workers", "estimated_input_tokens",
                                                 "expected_billed_input_tokens")},
           "workers": []}
    os.makedirs(raw, exist_ok=True)
    pending = []

    def save():
        json.dump(log, open(os.path.join(raw, "log.json"), "w"), indent=1)

    for n in range(1, packets + 1):
        rec = worker(args.agent, args.model, work, raw, "p%d" % n, fill(g["packet"], n))
        check(work, head, rec)
        pending += [line.strip() for line in rec["reply"].splitlines() if line.strip().lower().startswith("pending:")]
        rec["unowned"] = evermap(work, "packet", str(n), "--unowned").get("unowned_total")
        log["workers"].append(rec)
        save()
        if rec["unowned"]:
            text = fill(g["packet"], n) + "\n\n" + fill(g["continuation"], n)
            rec2 = worker(args.agent, args.model, work, raw, "p%d-cont" % n, text)
            check(work, head, rec2)
            pending += [l.strip() for l in rec2["reply"].splitlines() if l.strip().lower().startswith("pending:")]
            rec2["unowned"] = evermap(work, "packet", str(n), "--unowned").get("unowned_total")
            log["workers"].append(rec2)
            save()
        if not rec["head_unchanged"] or rec["dirty"]:
            log["stopped"] = "packet %d changed HEAD or the tree outside .evermap/" % n
            save()
            sys.exit(log["stopped"])

    if (est.get("final_pass") or {}).get("workers"):
        block = "\n".join(pending)[:6000] or "none"
        text = fill(g["final"]) + "\n\nPending from workers:\n" + block
        rec = worker(args.agent, args.model, work, raw, "final", text)
        check(work, head, rec)
        log["workers"].append(rec)
    log["pending_lines"] = len(pending)
    log["sync"] = evermap(work, "sync", "--mode", "init").get("synced") is not None
    rendered = evermap(work, "render")
    log["render"] = {"thin": rendered.get("thin"), "kinds": rendered.get("kinds")}
    log["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    for rec in log["workers"]:
        rec["reply_chars"] = len(rec.pop("reply", "") or "")
    save()
    subprocess.run([sys.executable, os.path.join(HERE, "score.py"), "--run", args.run], check=False)


if __name__ == "__main__":
    main()
