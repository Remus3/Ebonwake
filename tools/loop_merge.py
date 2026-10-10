"""EW loop merge: the gate-verdict cache and the Tick's merge into main as
MergeMixin (plan 107 split of tools/ew_loop.py, which re-exports every name
here). Every side effect goes through self.d (Deps). Stdlib only."""

import datetime as _dt
import json
from pathlib import Path

from loop_base import (
    GATE_CACHE_MAX, GATE_CACHE_REL, IN_FLIGHT, KEEP_REF, MAX_ROUNDS, MERGE_LOCK_REL,
    RED_TTL_S, RESOLVABLE, ROADMAP_REL, ascii_text, atomic_write, epoch_of, iso,
    read_json,
)
from loop_roadmap import base_kind, flip_roadmap, resolve_roadmap


# ---------------------------------------------------------------- gate verdicts

class GateCache:
    """Plan 096: gate verdicts by tree id, ops/loop/control/gate_verdicts.json
    in the main checkout, newest GATE_CACHE_MAX kept. Plan 098: written by
    parallel review workers and the push worker too; each write is atomic, a
    racing read-modify-write can only drop an entry (that tree is re-gated),
    never record a wrong verdict."""

    def __init__(self, root):
        self.path = Path(root) / GATE_CACHE_REL

    def _trees(self):
        doc = read_json(self.path, {})
        trees = doc.get("trees") if isinstance(doc, dict) else None
        return dict(trees) if isinstance(trees, dict) else {}

    def get(self, key, now):
        """The usable entry for key: green, or red younger than RED_TTL_S."""
        e = self._trees().get(key) if key else None
        if not isinstance(e, dict) or not isinstance(e.get("ok"), bool):
            return None
        if not e["ok"]:
            ts = epoch_of(e.get("ts"))
            if ts is None or now - ts > RED_TTL_S:
                return None
        return e

    def put(self, key, ok, detail, where, now, via=None):
        trees = self._trees()
        trees.pop(key, None)  # re-insert: dict order is recency
        e = {"ok": bool(ok), "detail": ascii_text(detail or "", 300), "ts": iso(now),
             "where": ascii_text(where, 80)}
        if via:
            e["via"] = via
        trees[key] = e
        keep = dict(list(trees.items())[-GATE_CACHE_MAX:])
        atomic_write(self.path, json.dumps({"trees": keep}, indent=1))
        return e

    def carry(self, src, dst, git, cwd, now):
        """Record dst green on src's green verdict when the two trees differ
        only in the ROADMAP (the loop's own row flip at merge). True if
        dst is green afterwards."""
        e = self.get(src, now) if src and dst else None
        if not e or not e["ok"]:
            return False
        if src == dst:
            return True
        r = git(["diff", "--name-only", src, dst], cwd)
        if r.returncode != 0:
            return False
        names = {n.strip() for n in (r.stdout or "").splitlines() if n.strip()}
        if not names <= {ROADMAP_REL.as_posix()}:
            return False
        self.put(dst, True, e.get("detail"), "carry", now, via=src)
        return True


class MergeMixin:
    """Plan 107: mixed into ew_loop.Tick. The Tick's merge into main, ROADMAP
    flip and kept commits."""

    def merge(self, rec):
        """Plan 098: every merge into main holds MERGE_LOCK_REL, whoever calls
        it (review worker, the tick's salvage path); a busy lock defers the
        merge and the item stays committed for the next worker."""
        try:
            with self.d.lock(self.root / MERGE_LOCK_REL):
                return self._merge(rec)
        except self.d.watch.LockBusy:
            self.step(f"{rec['id']}: merge lock busy, merge deferred")

    def _merge(self, rec):
        main = self.d.main_tree
        br = self.d.git(["rev-parse", "--abbrev-ref", "HEAD"], main).stdout.strip()
        if br != "main" or self.dirty(main):
            self.step(f"{rec['id']}: main not clean on main, merge deferred")
            return
        lane = Path(rec["worktree"]).name
        g = self.d.git
        if g(["merge-base", "--is-ancestor", rec["commit"], "HEAD"], main).returncode == 0:
            # already in main (a crash after the merge commit): re-run is a no-op
            rec["state"] = "merged"
            self.drop_keep(rec)
            self.step(f"{rec['id']}: already merged")
            self.items.put(rec)
            return
        r = g(["merge", "--no-ff", "--no-commit", "-q", rec["commit"]], main)
        why, flipped_row = None, False
        if r.returncode != 0 and not self.resolve_roadmap_conflict(main):
            why = "merge conflict"
        if why is None and base_kind(rec) == "plan":
            rounds = rec.get("rounds", 0)
            date = _dt.datetime.fromtimestamp(self.d.clock()).strftime("%Y-%m-%d")
            rm = main / ROADMAP_REL
            try:
                text = rm.read_text(encoding="utf-8")
                flipped = flip_roadmap(text, rec["id"],
                                       f"done {date} (loop; refute {rounds}/{MAX_ROUNDS} "
                                       f"{rec.get('verdict', 'PASS')})")
                if flipped != text:
                    atomic_write(rm, flipped)
                    if g(["add", ROADMAP_REL.as_posix()], main).returncode != 0:
                        why = "roadmap stage refused"
                    flipped_row = True
            except OSError:
                pass
        state = "merge-conflict"
        if why is None:
            msg = (f"merge {lane}: {rec['label']}\n\nrefute-rounds: "
                   f"{rec.get('rounds', 0)}/{MAX_ROUNDS}\n")
            c = g(["commit", "-q", "-F", "-"], main, input=msg)
            if c.returncode != 0:
                # main's own hook (leak sweep) refused clean merged content: a
                # resolve run would merge cleanly, change nothing and be refused
                # again, so it is parked for a session with the hook output
                why, state = "merge commit refused", "merge-refused"
                out = ((c.stderr or "") + (c.stdout or "")).strip()
                rec["error"] = ascii_text(f"main merge commit refused: {out[-260:]}", 300)
        if why:
            g(["merge", "--abort"], main)
            rec["state"] = state
            self.keep(rec)
            self.step(f"{rec['id']}: {why}, aborted")
        else:
            rec["state"] = "merged"
            if flipped_row:  # plan 019 4c: when this row became [x]
                rec["merged_at"] = iso(self.d.clock())
            self.drop_keep(rec)
            self.step(f"{rec['id']}: merged")
            self.carry_verdict(rec, main)
        self.items.put(rec)

    def carry_verdict(self, rec, main):
        """Plan 096: main's new tree inherits the lane tree's green verdict
        when the merge added only the ROADMAP row flip (main had not moved),
        so push() does not gate the same code twice."""
        g = self.d.git
        src = g(["rev-parse", f"{rec['commit']}^{{tree}}"], main)
        dst = g(["rev-parse", "HEAD^{tree}"], main)
        if src.returncode or dst.returncode:
            return
        src, dst = src.stdout.strip(), dst.stdout.strip()
        if src != dst and GateCache(self.root).carry(src, dst, g, main, self.d.clock()):
            self.step(f"{rec['id']}: gate verdict carried to main {dst[:12]}")

    def keep(self, rec):
        """Plan 058 step 1: point refs/ew/keep/<id> at the unmerged commit in
        the main checkout; only a ref that was written is recorded."""
        ref = KEEP_REF + rec["id"]
        if rec.get("commit") and self.d.git(["update-ref", ref, rec["commit"]],
                                            self.d.main_tree).returncode == 0:
            rec["keep_ref"] = ref

    def drop_keep(self, rec):
        ref = rec.pop("keep_ref", None)
        if ref:
            self.d.git(["update-ref", "-d", ref], self.d.main_tree)

    def settle_conflicts(self):
        """Plan 058, idempotent, before dispatch: a merge-conflict whose commit
        is already in main (merged by hand) is merged and its ref dropped; one
        without a ref (recorded before plan 058) gets one if its commit still
        exists. Any other record drops its ref once that commit is in main
        (failed / adjudicate / gave-up keep theirs for a session)."""
        if self.dry:
            return
        g, main = self.d.git, self.d.main_tree
        for rec in self.items.all().values():
            state = rec.get("state")
            if state in IN_FLIGHT or not rec.get("commit"):
                continue
            if state != "merge-conflict" and not rec.get("keep_ref"):
                continue
            in_main = g(["merge-base", "--is-ancestor", rec["commit"], "HEAD"],
                        main).returncode == 0
            if in_main and state in RESOLVABLE:
                rec["state"] = "merged"
                self.drop_keep(rec)
                self.step(f"{rec['id']}: conflict merged by hand, ref dropped")
            elif state == "merge-conflict" and not rec.get("keep_ref"):
                self.keep(rec)
                if not rec.get("keep_ref"):
                    continue
                self.step(f"{rec['id']}: kept under {rec['keep_ref']}")
            elif state != "merge-conflict" and in_main:
                self.drop_keep(rec)
            else:
                continue
            self.items.put(rec)

    def resolve_roadmap_conflict(self, main):
        """True when the only unmerged path is ROADMAP.md and the row-level
        resolver settles it (staged); False leaves the merge for --abort."""
        g, rel = self.d.git, ROADMAP_REL.as_posix()
        u = g(["diff", "--name-only", "--diff-filter=U"], main)
        if u.returncode != 0 or u.stdout.split() != [rel]:
            return False

        def stage(n):
            s = g(["show", f":{n}:{rel}"], main)
            return s.stdout if s.returncode == 0 else ""

        out = resolve_roadmap(stage(1), stage(2), stage(3))
        if out is None:
            return False
        atomic_write(main / ROADMAP_REL, out)
        if g(["add", rel], main).returncode != 0:
            return False
        self.step("ROADMAP-only conflict auto-resolved by row")
        return True
