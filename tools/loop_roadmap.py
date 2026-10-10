"""EW loop work list: ROADMAP rows, plan docs, dependencies, hand-off and
data items (plan 107 split of tools/ew_loop.py, which re-exports every name
here). Stdlib only."""

import contextlib
import hashlib
import json
import re
from pathlib import Path

from loop_base import (
    DEPENDS_RX, DEP_ID_RX, MAX_ATTEMPTS, ROADMAP_REL, ROW_RX, SKIP_TAGS, STOP,
)
from loop_prompts import resolve_prompt


# ---------------------------------------------------------------- work list

def roadmap_rows(text):
    """[{"id", "title", "status", "open"}] from the ROADMAP table."""
    rows = []
    for line in text.splitlines():
        m = ROW_RX.match(line)
        if m:
            rows.append({"id": m.group(1), "title": m.group(2), "status": m.group(3),
                         "open": m.group(3).startswith("[ ]")})
    return rows


def flip_roadmap(text, plan_id, note):
    """The ROADMAP text with row plan_id's "[ ] ..." status set to "[x] note"."""
    out = []
    for line in text.split("\n"):
        m = ROW_RX.match(line)
        if m and m.group(1) == plan_id and m.group(3).startswith("[ ]"):
            line = f"| {m.group(1)} | {m.group(2)} | [x] {note} |"
        out.append(line)
    return "\n".join(out)


def plan_depends(text):
    """The three-digit plan ids named by the first `Depends on:` line, in order
    ("none" or no such line -> [])."""
    m = DEPENDS_RX.search(text or "")
    out = []
    for dep in DEP_ID_RX.findall(m.group(1)) if m else ():
        if dep not in out:
            out.append(dep)
    return out


def plan_doc(root, plan_id):
    """docs/plans/<id>-*.md text, or None when there is none."""
    for p in sorted((Path(root) / "docs" / "plans").glob(f"{plan_id}-*.md")):
        with contextlib.suppress(OSError):
            return p.read_text(encoding="utf-8", errors="replace")
    return None


def base_kind(rec):
    """The item's own kind; a plan 058 resolve run keeps it in base_kind."""
    return rec.get("base_kind") or rec.get("kind")


# a resolve item copies its record minus these per-run fields
RUN_FIELDS = ("state", "pid", "rc", "error", "verdict", "verify_errors", "finished",
              "updated", "prompt", "worktree", "dispatched")


def resolve_item(rec):
    item = {k: v for k, v in rec.items() if k not in RUN_FIELDS}
    item.update(kind="resolve", base_kind=base_kind(rec))
    item["prompt"] = resolve_prompt(item)
    return item


def dependency_cycles(graph):
    """Each cycle of {id: [dep ids]} once, as [a, b, ..., a]."""
    cycles, state = [], {}

    def visit(node, stack):
        state[node] = 1
        stack.append(node)
        for dep in graph.get(node, ()):
            if state.get(dep) == 1:
                cycles.append(stack[stack.index(dep):] + [dep])
            elif dep in graph and not state.get(dep):
                visit(dep, stack)
        stack.pop()
        state[node] = 2

    for node in sorted(graph):
        if not state.get(node):
            visit(node, [])
    return cycles


def roadmap_flip_time(git, main, dep):
    """ISO commit time of the first-parent commit on main that introduced row
    dep's `[x]` line (the --no-ff merge commit for a loop merge), or None.
    Fallback for dependencies merged before merge() recorded merged_at."""
    r = git(["log", "-m", "--first-parent", "-1", "--format=%cI",
             f"-G^\\| {dep} \\|.*\\[x\\]", "--", ROADMAP_REL.as_posix()], main)
    out = (r.stdout or "").strip() if r.returncode == 0 else ""
    return out.splitlines()[0] if out else None


def resolve_roadmap(base, ours, theirs):
    """Row-level three-way merge of ROADMAP.md (fix-0130), or None. Rows are
    keyed by plan id: a row only the lane changed or added takes the lane's
    line, a row both sides changed keeps main's (main is the ledger). Any
    prose (non-row) change on both sides is not guessed at: None."""
    def split(text):
        rows, prose = {}, []
        for line in text.split("\n"):
            m = ROW_RX.match(line)
            if m:
                rows[m.group(1)] = line
            else:
                prose.append(line)
        return rows, prose

    (b_rows, b_prose), (o_rows, o_prose), (t_rows, t_prose) = \
        split(base), split(ours), split(theirs)
    if t_prose != b_prose and t_prose != o_prose:
        return None
    lines = ours.split("\n")
    for iid, line in t_rows.items():
        if line == b_rows.get(iid):
            continue
        if iid not in o_rows:
            at = [i for i, x in enumerate(lines) if ROW_RX.match(x)]
            last = at[-1] if at else len(lines) - 1
            lines.insert(last + 1, line)
            o_rows[iid] = line
        elif o_rows[iid] == b_rows.get(iid):
            lines[lines.index(o_rows[iid])] = line
    for iid, line in b_rows.items():
        if iid not in t_rows and o_rows.get(iid) == line:
            lines.remove(line)
    return "\n".join(lines)


def _norm_words(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def handoff_items(text):
    """Bullets under "Carried forward" as [{"id", "text", "skip"}]; skip is
    None or the tag that keeps the loop off it (operator / other-tree / info)."""
    items, cur, inside = [], None, False
    for line in text.splitlines():
        if line.lower().startswith("carried forward"):
            inside = True
            continue
        if not inside:
            continue
        if line.startswith("- "):
            cur = [line[2:].strip()]
            items.append(cur)
        elif line.startswith("  ") and cur is not None and line.strip():
            cur.append(line.strip())
        elif line.strip() and not line.startswith(" "):
            break
    out = []
    for parts in items:
        body = " ".join(parts)
        skip = next((tag for tag, rx in SKIP_TAGS if rx.search(body)), None)
        hid = "H" + hashlib.sha1(" ".join(_norm_words(body)).encode()).hexdigest()[:6]
        out.append({"id": hid, "text": body, "skip": skip})
    return out


VERDICTS_REL = ("ops", "runtime", "data_verdicts.json")


_KEY_RX = re.compile(r"^[A-Za-z0-9_./-]{1,80}\.json#[A-Za-z0-9_./-]{1,80}$")


def data_items(main):
    """Plan 085: [{"id", "text", "skip"}] - one hand-off-style data item per
    row the runtime verdicts mark contradicted. The quoted evidence is page
    text (ASCII, <= 200 chars), handed over as data, never as an order."""
    try:
        doc = json.loads(Path(main).joinpath(*VERDICTS_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    verdicts = doc.get("verdicts") if isinstance(doc, dict) else None
    out = []
    for key, v in sorted(verdicts.items() if isinstance(verdicts, dict) else ()):
        if not (isinstance(v, dict) and v.get("verdict") == "contradicted" and _KEY_RX.match(key)):
            continue
        ev = v.get("evidence") if isinstance(v.get("evidence"), str) else ""
        ev = "".join(c for c in ev if 32 <= ord(c) < 127).replace('"', "'")[:200]
        date = v.get("date") if isinstance(v.get("date"), str) else "?"
        url = v.get("url") if isinstance(v.get("url"), str) else ""
        file = key.split("#", 1)[0]
        text = (f"DATA: tracked row {key} is contradicted by the official patch notes "
                f"{date[:10]} ({url[:120]}); quoted evidence line: \"{ev}\". Edit that row in "
                f"server/ew/data/{file} to the patch-notes value, update its verify hint "
                f"and verified field (plan 085; data changes stay plan-reviewed).")
        hid = "D" + hashlib.sha1(f"{key}|{date}|{ev}".encode()).hexdigest()[:6]
        out.append({"id": hid, "text": text, "skip": None})
    return out


def title_tokens(title):
    return {w for w in _norm_words(title) if len(w) > 2 and w not in STOP}


def is_duplicate(title, existing, threshold=0.6):
    """Normalized token overlap |A&B| / min(|A|, |B|) >= threshold."""
    a = title_tokens(title)
    for other in existing:
        b = title_tokens(other)
        if a and b and len(a & b) / min(len(a), len(b)) >= threshold:
            return other
    return None


def plan_titles(root):
    titles = {}
    for p in sorted((Path(root) / "docs" / "plans").glob("[0-9][0-9][0-9]-*.md")):
        first = p.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
        titles[p.name[:3]] = re.sub(r"^#\s*Plan\s*\d+\s*-\s*", "", first).strip()
    return titles


def dispatchable(rec, open_ids=()):
    """paused (the lane worker found HALT / backoff / runs cap before it ran)
    costs no attempt; refused / lost get MAX_ATTEMPTS dispatches; blocked
    (plan 019) waits until no id it needs is an open ROADMAP row, for at most
    MAX_ATTEMPTS blocked runs; merge-conflict (plan 058) with a kept commit
    gets MAX_ATTEMPTS resolve runs."""
    if rec is not None and rec.get("state") == "merge-conflict":
        return bool(rec.get("keep_ref")) and rec.get("resolve_runs", 0) < MAX_ATTEMPTS
    if rec is not None and rec.get("state") == "blocked":
        return (rec.get("blocked_runs", 0) < MAX_ATTEMPTS
                and not set(rec.get("needs") or ()) & set(open_ids))
    return rec is None or rec.get("state") == "paused" or (
        rec.get("state") in ("refused", "lost") and rec.get("attempts", 0) < MAX_ATTEMPTS)
