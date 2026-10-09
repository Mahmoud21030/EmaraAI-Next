"""Quality gates, evidence, hidden verification and scoring (Phase 5; QUALITY_GATES.md, AGENT_AND_MODEL_SCORING.md,
EVALUATION_LAB.md).

No task is accepted because the author says it is done:
  Gate 1  every acceptance criterion has at least one evidence item
  Gate 2  every referenced artifact exists and still has the hash it was stored with
  Gate 5  an independent verifier (never the author) re-runs checks in a clean workspace
  Gate 6  hidden checks: the master stores them on the task; the author's tools never show them
  Gate 7  the diff is scanned for secrets and debug leftovers
A gate can only be skipped with a waiver (gate, reason, approver). Submissions are frozen as an evidence set with a hash.

Scoring keeps identities and routes apart: reputation events feed a competence score per member and per route, and the
router's preference can be set from it.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path

from .errors import Conflict, Forbidden, InvalidInput, NotFound
from .ids import new_id
from .kernel import Kernel
from .store import dumps

SECRET_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "Anthropic API key"),
    (re.compile(r"sk-[A-Za-z0-9]{32,}"), "API key"),
    (re.compile(r"ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"), "GitHub token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"(?i)(password|passwd|secret|api_key)\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"), "hard-coded secret"),
]
DEBUG_PATTERNS = [(re.compile(r"^\+.*\b(breakpoint\(\)|pdb\.set_trace\(\)|debugger;|console\.log\()"), "debug leftover")]

EVENT_WEIGHTS = {"accepted_first_try": 1.0, "accepted_after_changes": 0.5, "changes_requested": -0.5, "verification_failed": -1.0,
                 "defect_after_acceptance": -2.0, "eval_pass": 0.5, "eval_fail": -0.5}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_diff(diff: str) -> list[dict]:
    findings = []
    for n, line in enumerate(diff.splitlines(), 1):
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for rx, what in SECRET_PATTERNS:
            if rx.search(line):
                findings.append({"gate": "diff", "severity": "block", "what": what, "line": n})
        for rx, what in DEBUG_PATTERNS:
            if rx.search(line):
                findings.append({"gate": "diff", "severity": "warn", "what": what, "line": n})
    return findings


class Quality:
    def __init__(self, kernel: Kernel, root: str | Path, *, workspaces=None, runner=None):
        self.k = kernel
        self.root = Path(root).resolve()
        self.ws, self.runner = workspaces, runner

    # ------------------------------------------------------------------ artifacts
    def store_artifact(self, project_id: str, src: str | Path, *, kind: str = "file", task_id: str | None = None,
                       attempt_id: str | None = None, producer: str = "") -> dict:
        src = Path(src)
        if not src.is_file():
            raise NotFound(f"{src} does not exist.")
        aid = new_id("F")
        dest = self.root / "artifacts" / project_id / (task_id or "_") / (attempt_id or "_") / f"{aid}-{src.name}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        dest.chmod(0o444)                                      # immutable by convention
        digest = sha256_file(dest)
        with self.k.db.tx():
            self.k.db.run("INSERT INTO artifacts (id, project_id, task_id, attempt_id, kind, name, path, sha256, size, producer, created_at) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?)", aid, project_id, task_id, attempt_id, kind, src.name, str(dest), digest,
                          dest.stat().st_size, producer, self.k.clock.now())
            self.k.event("artifact.stored", subject=aid, project_id=project_id, sha256=digest)
        return {"id": aid, "sha256": digest, "size": dest.stat().st_size}

    def artifact_ok(self, artifact_id: str) -> tuple[bool, str]:
        a = self.k.db.one("SELECT * FROM artifacts WHERE id = ?", artifact_id)
        if not a:
            return False, "not stored"
        p = Path(a["path"])
        if not p.exists():
            return False, "file missing"
        if a["size"] == 0:
            return False, "empty"
        return (True, "") if sha256_file(p) == a["sha256"] else (False, "changed after it was stored")

    # ------------------------------------------------------------------ hidden checks / waivers
    def set_hidden_checks(self, task_id: str, checks: list[dict], *, by: str) -> None:
        """checks: [{"name": "...", "run": "<command>"}]. Stored apart from the task: the author never sees them."""
        t = self.k.task(task_id)
        if t["assignee"] == by:
            raise Forbidden("The author cannot write the hidden checks for their own task.")
        with self.k.db.tx():
            self.k.db.run("INSERT OR REPLACE INTO hidden_checks (task_id, checks, created_by, created_at) VALUES (?,?,?,?)",
                          task_id, dumps(checks), by, self.k.clock.now())
            self.k.event("quality.hidden_checks", subject=task_id, project_id=t["project_id"], count=len(checks))

    def waive(self, task_id: str, gate: str, *, reason: str, approver: str, expires_at: float | None = None) -> dict:
        if not reason.strip():
            raise InvalidInput("A waiver needs a reason.")
        t = self.k.task(task_id)
        if approver == t["assignee"]:
            raise Forbidden("The author cannot waive a gate on their own task.")
        wid = new_id("WV")
        with self.k.db.tx():
            self.k.db.run("INSERT INTO waivers (id, task_id, gate, reason, approver, expires_at, created_at) VALUES (?,?,?,?,?,?,?)",
                          wid, task_id, gate, reason, approver, expires_at, self.k.clock.now())
            self.k.event("quality.waiver", subject=wid, project_id=t["project_id"], gate=gate, approver=approver)
        return {"id": wid}

    def _waived(self, task_id: str, gate: str) -> bool:
        w = self.k.db.one("SELECT * FROM waivers WHERE task_id = ? AND gate = ? AND (expires_at IS NULL OR expires_at > ?)",
                          task_id, gate, self.k.clock.now())
        return bool(w)

    # ------------------------------------------------------------------ submission
    def submit(self, attempt_id: str, fence: int, *, summary: str, items: list[dict], diff: str = "", revision: str = "",
               actor: str = "") -> dict:
        """items: [{"criterion": "<text from acceptance>", "evidence": ["F-artifact" | "text"]}]"""
        a = self.k.attempt(attempt_id)
        t = self.k.task(a["task_id"])
        problems, findings = [], []
        # Gate 1 - every criterion covered
        covered = {i.get("criterion") for i in items if i.get("evidence")}
        missing = [c for c in t["acceptance"] if c not in covered]
        if missing and not self._waived(t["id"], "criteria"):
            problems.append(f"no evidence for: {'; '.join(missing)}")
        # Gate 2 - artifacts exist and are unchanged
        for i in items:
            for ev in i.get("evidence", []):
                if isinstance(ev, str) and ev.startswith("F-"):
                    ok, why = self.artifact_ok(ev)
                    if not ok:
                        problems.append(f"artifact {ev}: {why}")
        # Gate 7 - diff scan
        findings = scan_diff(diff)
        if any(f["severity"] == "block" for f in findings) and not self._waived(t["id"], "diff"):
            problems.append("the diff contains secrets: " + ", ".join(sorted({f["what"] for f in findings if f["severity"] == "block"})))
        if problems:
            raise Conflict("The submission does not pass the quality gates.", fix=" | ".join(problems), findings=findings)
        frozen = dumps({"items": items, "revision": revision, "summary": summary})
        with self.k.db.tx():
            self.k.db.run("INSERT OR REPLACE INTO evidence_sets (attempt_id, items, revision, findings, sha256, created_at) VALUES (?,?,?,?,?,?)",
                          attempt_id, dumps(items), revision, dumps(findings), hashlib.sha256(frozen.encode()).hexdigest(), self.k.clock.now())
            flat = [f"{i['criterion']}: {e}" for i in items for e in i.get("evidence", [])]
            out = self.k.submit(attempt_id, fence, summary=summary, evidence=flat or [summary], actor=actor)
        return {**out, "warnings": [f for f in findings if f["severity"] == "warn"]}

    # ------------------------------------------------------------------ independent verification
    def verify(self, task_id: str, *, verifier: str, repo: str = "") -> dict:
        """Run the hidden checks against the submitted revision in a fresh workspace owned by the verifier."""
        t = self.k.task(task_id)
        if verifier == t["assignee"]:
            raise Forbidden("The author cannot verify their own work.")
        a = self.k.db.one("SELECT * FROM attempts WHERE task_id = ? AND status = 'SUBMITTED' ORDER BY number DESC LIMIT 1", task_id)
        if not a:
            raise Conflict(f"{task_id} has nothing submitted to verify.")
        hc = self.k.db.one("SELECT * FROM hidden_checks WHERE task_id = ?", task_id)
        checks = json.loads(hc["checks"]) if hc else []
        results = []
        if checks:
            if not (self.ws and self.runner):
                raise Conflict("No workspace runner is configured for verification.")
            src_ws = self.k._get("workspaces", a["workspace_id"]) if a["workspace_id"] else None
            vid = new_id("V")
            # its own branch, started from what the author pushed: never the author's worktree or branch
            src_branch = (src_ws or {}).get("branch", "")
            repo = repo or (src_ws or {}).get("repo", "")
            from .workspace import git
            pushed = src_branch and git(self.ws.root, "ls-remote", "--heads", repo, src_branch, check=False)
            base = f"origin/{src_branch}" if pushed else (src_ws or {}).get("base_revision", "")   # nothing pushed: author changed nothing
            ws = self.ws.provision(t["project_id"], task_id, vid, repo=repo, base=base, branch=f"emara-verify/{vid}", policy="EPHEMERAL")
            try:
                for c in checks:
                    r = self.runner.run(ws["id"], c["run"], timeout=float(c.get("timeout", 600)), owner=verifier)
                    results.append({"name": c["name"], "passed": r.ok, "exit_code": r.exit_code, "tail": (r.stdout + r.stderr)[-1500:]})
            finally:
                try:
                    self.ws.cleanup(ws["id"], outcome="accepted")
                except Exception:  # noqa: BLE001 - the janitor retries
                    pass
        passed = all(r["passed"] for r in results)
        with self.k.db.tx():
            self.k.db.run("INSERT INTO verifications (id, attempt_id, verifier, passed, results, created_at) VALUES (?,?,?,?,?,?)",
                          new_id("VR"), a["id"], verifier, int(passed), dumps(results), self.k.clock.now())
            self.k.event("quality.verified", subject=a["id"], project_id=t["project_id"], passed=passed, checks=len(results))
        return {"attempt_id": a["id"], "passed": passed, "results": results}

    def review(self, team, project_id: str, task_id: str, *, by: str, accept: bool, note: str = "") -> dict:
        """Review with the gates: acceptance needs a passing verification when hidden checks exist (unless waived)."""
        t = self.k.task(task_id)
        a = self.k.db.one("SELECT * FROM attempts WHERE task_id = ? AND status = 'SUBMITTED' ORDER BY number DESC LIMIT 1", task_id)
        if accept and a and self.k.db.one("SELECT 1 FROM hidden_checks WHERE task_id = ?", task_id) and not self._waived(task_id, "verification"):
            v = self.k.db.one("SELECT * FROM verifications WHERE attempt_id = ? ORDER BY created_at DESC LIMIT 1", a["id"])
            if not v:
                raise Conflict("This task has hidden checks: verify it first.", fix="quality.verify(task_id, verifier=...)")
            if not v["passed"]:
                self.event(project_id, "member", t["assignee"], "verification_failed", task_id)
                raise Conflict("The independent verification failed.", fix="Send it back with accept=false.")
        prior = self.k.db.one("SELECT COUNT(*) n FROM attempts WHERE task_id = ? AND status = 'REJECTED'", task_id)["n"]
        out = team.review(project_id, task_id, by=by, accept=accept, note=note)
        kind = ("accepted_first_try" if not prior else "accepted_after_changes") if accept else "changes_requested"
        self.event(project_id, "member", t["assignee"], kind, task_id)
        if a and a["route"]:
            self.event(project_id, "route", a["route"], kind, task_id)
        return out

    def defect(self, project_id: str, task_id: str, *, found_by: str, description: str) -> dict:
        """A defect found after acceptance lowers the author's (and route's) score and opens rework."""
        t = self.k.task(task_id)
        if t["status"] != "DONE":
            raise Conflict("Defects are for accepted work; send open work back with a review instead.")
        a = self.k.db.one("SELECT * FROM attempts WHERE task_id = ? AND status = 'ACCEPTED' ORDER BY number DESC LIMIT 1", task_id)
        self.event(project_id, "member", t["assignee"], "defect_after_acceptance", task_id)
        if a and a["route"]:
            self.event(project_id, "route", a["route"], "defect_after_acceptance", task_id)
        fix = self.k.create_task(project_id, f"Fix defect in {task_id}: {description[:60]}", instructions=description,
                                 assignee=t["assignee"], actor=found_by)
        return {"defect_task": fix["id"]}

    # ------------------------------------------------------------------ scoring
    def event(self, project_id: str | None, kind: str, subject: str, event: str, task_id: str | None = None) -> None:
        if not subject:
            return
        with self.k.db.tx():
            self.k.db.run("INSERT INTO reputation (project_id, subject_kind, subject, event, weight, task_id, created_at) VALUES (?,?,?,?,?,?,?)",
                          project_id, kind, subject, event, EVENT_WEIGHTS[event], task_id, self.k.clock.now())

    def score(self, kind: str, subject: str, *, half_life_days: float = 30.0) -> dict:
        """Decayed score in [-1, 1] plus the evidence it rests on (explainable, cold start = 0 with n = 0)."""
        rows = self.k.db.all("SELECT * FROM reputation WHERE subject_kind = ? AND subject = ? ORDER BY created_at", kind, subject)
        now = self.k.clock.now()
        num = den = 0.0
        for r in rows:
            w = 0.5 ** ((now - r["created_at"]) / 86400 / half_life_days)
            num += w * r["weight"]
            den += w * 2.0
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["event"]] = counts.get(r["event"], 0) + 1
        return {"subject": subject, "score": round(num / den, 3) if den else 0.0, "n": len(rows), "events": counts}

    def apply_to_router(self, router) -> dict:
        """Router preference from route scores (only routes with evidence move)."""
        out = {}
        for rid, r in router.routes.items():
            s = self.score("route", rid)
            if s["n"]:
                r.preference = s["score"]
                out[rid] = s["score"]
        return out


class Lab:
    """Benchmark runs: the same cases on several routes, results stored, pass rate/cost/time per route."""

    def __init__(self, kernel: Kernel, quality: Quality):
        self.k, self.q = kernel, quality

    async def run(self, cases: list[dict], routes: dict, solve) -> dict:
        """solve(route_id, case) -> {"passed": bool, "cost": float, "detail": str} (async or sync)."""
        run_id = new_id("EV")
        for rid in routes:
            for case in cases:
                t0 = time.monotonic()
                try:
                    r = solve(rid, case)
                    if hasattr(r, "__await__"):
                        r = await r
                except Exception as e:  # noqa: BLE001 - a crash is a failed case, recorded
                    r = {"passed": False, "detail": f"{type(e).__name__}: {e}"}
                with self.k.db.tx():
                    self.k.db.run("INSERT INTO eval_results (run_id, case_id, route, passed, seconds, cost, detail, created_at) VALUES (?,?,?,?,?,?,?,?)",
                                  run_id, case["id"], rid, int(bool(r.get("passed"))), time.monotonic() - t0, float(r.get("cost", 0)),
                                  str(r.get("detail", ""))[:2000], self.k.clock.now())
                self.q.event(None, "route", rid, "eval_pass" if r.get("passed") else "eval_fail")
        return {"run_id": run_id, "summary": self.summary(run_id)}

    def summary(self, run_id: str) -> dict:
        rows = self.k.db.all("SELECT route, COUNT(*) n, SUM(passed) ok, SUM(cost) cost, AVG(seconds) secs FROM eval_results "
                             "WHERE run_id = ? GROUP BY route ORDER BY SUM(passed) DESC, SUM(cost)", run_id)
        return {r["route"]: {"pass_rate": r["ok"] / r["n"], "cases": r["n"], "cost": r["cost"], "avg_seconds": r["secs"]} for r in rows}
