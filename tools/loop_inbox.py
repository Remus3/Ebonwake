"""EW loop inbox: channel-note prompts, routed orders and the Tick's inbox
pass as InboxMixin (plan 107 split of tools/ew_loop.py, which re-exports
every name here). Every side effect goes through self.d (Deps). Stdlib only."""

import datetime as _dt
import hashlib
import json
import os
import re
import time
from pathlib import Path

from loop_base import (
    CODE, DELIVERY_REL, DONE_STATES, INBOX_REL, Items, LEGACY_LEDGER_REL, MAX_ROUNDS,
    NOTE_HEAD, NOTE_MAX, NOTE_STAMP, ORDERS_REL, WATCH_REL, ascii_text, atomic_write,
    iso, read_json, read_jsonl, with_hop,
)
from loop_prompts import GATES


# ---------------------------------------------------------------- inbox helpers

def inbox_prompt(name, text, context=None):
    return ("You are the Ebonwake (EW) session answering ONE channel note. Reply with the "
            "note body only: markdown, ASCII, first line '# From EW - ANSWER re " + name
            + "'. Answer what is asked; do not change files. Never put a directory name, "
            "account id or email in the reply." + (" " + context if context else "")
            + "\n\n--- NOTE " + name + " ---\n" + text[:NOTE_MAX] + "\n--- END NOTE ---")


def order_id(name):
    return "N" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]


# plan 091: lane briefs forbid ops/fleet_kit/ and CLAUDE.md and lanes have no
# .claude/ write grant, so an ORDER naming one of them is blocked by
# construction; it goes to the interactive session instead of a lane.
SESSION_TARGETS = (("ops/fleet_kit/", re.compile(r"(?<![\w.])ops[\\/]fleet_kit\b|"
                                                  r"\bkit[ _-]?v\d+\b", re.I)),
                   (".claude/", re.compile(r"(?<![\w.])\.claude[\\/]")),
                   ("CLAUDE.md", re.compile(r"(?<![\w.])CLAUDE\.md(?![\w.])")))


def session_paths(name, text):
    """The protected targets an order note (name + body) touches, fixed order."""
    blob = f"{name}\n{text or ''}"
    return [t for t, rx in SESSION_TARGETS if rx.search(blob)]


def order_prompt(order):
    return ("You are an Ebonwake (EW) build lane in a detached git worktree. Read "
            "CLAUDE.md first. Carry out the channel ORDER note below in THIS tree. Do "
            "every item that needs only repo files. An item needing a password, an OAuth "
            "grant, a download, a tool outside your allow list or a physical act is left "
            "undone and listed as BLOCKED (one line, the reason) in a section 'Order "
            + order["id"] + " - blocked items' at the end of docs/plans/ROADMAP.md. Do not "
            "write the ANSWER note; the loop writes it after merge. "
            + GATES.format(task=order["id"])
            + "\n\n--- NOTE " + order["note"] + " ---\n" + order.get("text", "")[:NOTE_MAX]
            + "\n--- END NOTE ---")


def queued_orders(root):
    doc = read_json(Path(root) / ORDERS_REL, {}) or {}
    return [o for o in doc.get("orders", []) if isinstance(o, dict) and o.get("id")]


def session_orders(root):
    """Plan 091: queued orders routed to the session and not yet marked done,
    each with its "needs" targets."""
    items = Items(root)
    out = []
    for o in queued_orders(root):
        hits = session_paths(o.get("note", ""), o.get("text", ""))
        if hits and not (items.get(o["id"]) or {}).get("session_done"):
            out.append(dict(o, needs=hits))
    return out


def session_done(root, oid, commit=None, clock=time.time):
    """Plan 091: the session marks a routed order done; the next tick answers
    it. None for an id that is not a queued order. A second call keeps the
    first record (idempotent)."""
    order = next((o for o in queued_orders(root) if o["id"] == oid), None)
    if order is None:
        return None
    items = Items(root)
    rec = items.get(oid) or {}
    if rec.get("session_done"):
        return rec
    rec.update(id=oid, kind="order", title=order.get("title", ""), note=order.get("note"),
               state="merged", verdict="session", session_done=True,
               commit=ascii_text(commit or "none", 80), finished=iso(clock()))
    return items.put(rec)


# Kit v11 ruling R1 + EW adjudication D3 (2026-10-08): EW's safety floors (Game
# ToS) live in prompts and code, not hooks, so triage runs bare. Reverses if a
# floor moves into a hook.
FLOORS_IN_HOOKS = False


def triage_spawn_kwargs(fi):
    """spawn() keywords for ONE triage run (kit v11+ fleet_inbox)."""
    return fi.triage_spawn_kwargs(FLOORS_IN_HOOKS)


class InboxMixin:
    """Plan 107: mixed into ew_loop.Tick. The Tick's inbox pass (b.): notes
    in, answers and orders out."""

    # -- b. inbox
    def inbox(self):
        inbox, outbox = self.cfg["inbox_dir"], self.cfg["outbox_dir"]
        if not inbox or not Path(inbox).is_dir():
            self.step("inbox unconfigured")
            return
        if not outbox:
            self.step("outbox unconfigured")
            return
        inbox, outbox = Path(inbox), Path(outbox)

        def fetch():
            files = [p for p in inbox.iterdir() if p.is_file() and not p.name.startswith(".")]
            return [p.name for p in sorted(files, key=lambda p: (p.stat().st_mtime, p.name))]

        state = self.d.watch.WatchState(self.root / WATCH_REL)
        if self.dry:
            seen = set(state.seen("inbox"))
            new = [n for n in fetch() if n not in seen]
            self.step(f"inbox: {len(new)} new (dry run)")
            return
        self.redeliver(outbox)

        def deliver(names):
            batch = {}  # destination -> [(note, answer text, incoming hop)]
            pending = [n for n in names if not self.answer(inbox / n, outbox, batch)]
            pending += self.send_batches(batch, outbox)
            if pending:  # left unseen: run_source re-offers them next tick
                return {"delivered": False, "detail": f"{len(pending)} pending"}
            return {"delivered": True}

        res = self.d.watch.run_source(state, "inbox", fetch, deliver,
                                      lambda s, n, d: {"delivered": True})
        outcome, detail = res["outcome"], res["detail"]
        if outcome == "deliver-failed":
            # Pending by design is not a delivery failure (2026-10-05 loop.json
            # read "deliver-failed 2 2 pending": two ORDERs awaiting lanes).
            n = int(detail.split()[0]) if detail[:1].isdigit() else -1
            failed = n - self.awaiting - self.capped - self.paused
            if n >= 0 and failed <= 0:
                outcome = "pending"
            detail = (f"pending: {self.awaiting} order(s) awaiting lane, {self.capped} "
                      f"capped, {self.paused} paused, {max(failed, 0)} failed")
        self.step(f"inbox: {outcome} {len(res['new'])} new; {detail}"[:200])

    def handled(self, name, outbox):
        """Already settled: in the kit seen ledger, or answered by a pre-v8 tick."""
        if name in self.fi.seen(self.root):
            return True
        legacy = read_jsonl(self.root / LEGACY_LEDGER_REL)  # plan 015 deviation 14e
        if any(d.get("note") == name for d in legacy):
            return True
        stem = Path(name).stem
        return outbox.is_dir() and any(p.name.endswith(f"-re-{stem}.md")
                                       for p in outbox.iterdir())

    def answer(self, path, outbox, batch):
        """True when the note needs nothing more (handled now or before); False =
        pending (re-offered next tick). FLEET item 14: skip / ack are ledger
        lines, ORDER / FIX / RULING escalate, anything else is triaged once."""
        name = path.name
        if self.handled(name, outbox):
            return True
        text = path.read_text(encoding="utf-8", errors="replace")
        head = text[:NOTE_HEAD]
        dec = self.fi.classify(name, CODE, head)
        skip = self.d.should_skip(name, CODE, head)
        # classify() decides escalation (class ORDER / FIX / RULING by the
        # note's own sender-class token); a filename that merely QUOTES an
        # order's name (an ACK or ANSWER re- it) is never escalated.
        if dec.action == self.fi.WORK:
            return self.answer_order(name, text, dec, outbox, batch)
        if skip or dec.action in (self.fi.SKIP, self.fi.ACK):
            self.fi.mark_seen(self.root, name, dec, clock=self.d.clock)
            self.step(f"inbox {dec.action} ({skip or dec.reason}): {name}")
            return True
        to = dec.sender or "MAIN"
        if to not in batch and self.cap.used() + len(batch) >= self.cap.cap:
            self.capped += 1
            self.step(f"inbox: daily note cap {self.cap.cap} reached")
            return False
        if self.blocked():
            self.paused += 1
            return False
        line = self.spawn(self.fi.triage_prompt(name, text[:NOTE_MAX]), note=name,
                          writes_code=False, kind="triage", **triage_spawn_kwargs(self.fi))
        if not line or line.get("rc") != 0 or line.get("result") is None:
            return False
        verdict, body = self.fi.parse_verdict(ascii_text(line["result"]))
        if verdict != "ANSWER":
            self.fi.mark_seen(self.root, name, dec, verdict=verdict, clock=self.d.clock)
            self.step(f"inbox triage {verdict} (ledger, no note): {name}")
            return True
        batch.setdefault(to, []).append((name, body, dec))
        return True

    def send_batches(self, batch, outbox):
        """ONE note per destination (kit batch_note); returns notes left pending."""
        pending = []
        stamp = _dt.datetime.fromtimestamp(self.d.clock()).strftime(NOTE_STAMP)
        for to, parts in batch.items():
            hop_n = self.fi.next_hop(max(dec.hop for _, _, dec in parts))
            fname, body, names = self.fi.batch_note(CODE, to, [(n, b) for n, b, _ in parts],
                                                    hop_n=hop_n, stamp=stamp)
            if not self.cap.allow("ANSWER"):
                self.capped += len(names)
                pending += names
                continue
            if not self.write_note(outbox / fname, body):
                pending += names
                continue
            self.cap.record(fname, "ANSWER", to, parts=len(parts))
            for n, _, dec in parts:
                self.fi.mark_seen(self.root, n, dec, verdict="ANSWER", clock=self.d.clock)
            n_ok = int(self.deliver_note(outbox / fname, to))
            self.step(f"inbox answered: {', '.join(names)} -> {to} "
                      f"({n_ok}/1 reached)"[:200])
        return pending

    # -- plan 093: destination copies (FLEET-COMMON 7: re-hash, N/M reached)
    def dest_inbox(self, to):
        """The destination tree's inbox dir for code `to`, or None: config
        loop.dest_inboxes first, then the fleet roster (loop.roster, else
        FLEET_ROSTER) entry's inbox, else its root/moon_sync_inbox."""
        to = str(to or "").upper()
        if self.cfg["dest_inboxes"].get(to):
            return Path(self.cfg["dest_inboxes"][to])
        roster = self.cfg.get("roster") or self.d.roster_path
        doc = read_json(Path(roster), {}) if roster else {}
        for r in (doc or {}).get("repos") or []:
            if isinstance(r, dict) and str(r.get("code", "")).upper() == to:
                if r.get("inbox"):
                    return Path(r["inbox"])
                if r.get("root"):
                    return Path(r["root"]) / INBOX_REL
        return None

    def deliver_note(self, src, to, record_failure=True):
        """Byte-copy an outbox note into `to`'s inbox, re-hash the copy against
        the outbox copy and append a delivery ledger line. True = reached.
        An existing copy with the same bytes counts as reached; one with other
        bytes is never overwritten."""
        src = Path(src)
        data = src.read_bytes()
        want = hashlib.sha256(data).hexdigest()
        inbox = self.dest_inbox(to)
        dest, reached, detail = None, False, ""
        own = Path(self.cfg["inbox_dir"]).resolve() if self.cfg["inbox_dir"] else None
        if inbox is None:
            detail = f"no inbox known for {to}"
        elif not inbox.is_dir():
            detail = "destination inbox missing"
        elif own is not None and inbox.resolve() == own:
            detail = "destination is EW's own inbox"
        else:
            dest = inbox / src.name
            try:
                if not dest.exists():
                    tmp = inbox / f".{src.name}.{os.getpid()}.tmp"  # dot: never scanned
                    tmp.write_bytes(data)
                    tmp.replace(dest)
                got = hashlib.sha256(dest.read_bytes()).hexdigest()
                reached = got == want
                detail = "" if reached else "destination copy hash differs"
            except OSError as exc:
                detail = ascii_text(f"copy failed: {exc}", 200)
        if reached or record_failure:
            line = {"ts": iso(self.d.clock()), "note": src.name, "to": to,
                    "sha256": want, "reached": reached, "count": f"{int(reached)}/1"}
            if dest is not None:
                line["dest_name"] = dest.name
            if detail:
                line["detail"] = detail
            path = self.root / DELIVERY_REL
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="ascii", newline="\n") as fh:
                fh.write(json.dumps(line, sort_keys=True) + "\n")
        if not reached:
            self.step(f"delivery {src.name} -> {to}: {detail}"[:200])
        return reached

    def redeliver(self, outbox):
        """Retry every outbox note whose latest ledger line is not reached
        (idempotent: a reached note is never copied again)."""
        last = {}
        for line in read_jsonl(self.root / DELIVERY_REL):
            if line.get("note"):
                last[line["note"]] = line
        todo = [d for d in last.values() if not d.get("reached")
                and (outbox / d["note"]).is_file()]
        if not todo:
            return
        ok = sum(self.deliver_note(outbox / d["note"], d.get("to"), record_failure=False)
                 for d in todo)
        self.step(f"inbox: redelivered {ok}/{len(todo)} reached")

    def write_note(self, dest, body):
        body = ascii_text(body).rstrip("\n") + "\n"
        atomic_write(dest, body)
        return hashlib.sha256(dest.read_bytes()).hexdigest() == \
            hashlib.sha256(body.encode("ascii")).hexdigest()

    def answer_order(self, name, text, dec, outbox, batch):
        """An escalated ORDER / FIX / RULING: queued as a lane item, answered
        (one note, HOP incoming + 1, counted by OutboundCap) once it is done."""
        oid = order_id(name)
        rec = self.items.get(oid)
        # plan 091: a session-routed order closes only on the session's mark
        done = (bool(rec) and bool(rec.get("session_done"))) if session_paths(name, text) \
            else bool(rec) and rec.get("state") in DONE_STATES + ("adjudicate",)
        if not done:
            self.queue_order(oid, name, text)
            self.awaiting += 1
            return False  # answered after the lane item is done
        context = (f"EW's loop carried this order out as lane item {oid}: state "
                   f"{rec.get('state')}, verdict {rec.get('verdict', 'none')}, "
                   f"refute-rounds {rec.get('rounds', 0)}/{MAX_ROUNDS}, commit "
                   f"{rec.get('commit', 'none')}. Mark each item DONE in that commit, "
                   "or BLOCKED / NOT-APPLICABLE with the reason.")
        if self.cap.used() + len(batch) >= self.cap.cap:  # batches hold their slots
            self.capped += 1
            self.step(f"inbox: daily note cap {self.cap.cap} reached")
            return False
        if self.blocked():
            self.paused += 1
            return False
        line = self.spawn(inbox_prompt(name, text, context), note=name, writes_code=False,
                          **self.route("inbox", name), timeout=1800,
                          kind="inbox")
        reply = (line or {}).get("result")
        if not line or line.get("rc") != 0 or not reply:
            return False
        stamp = _dt.datetime.fromtimestamp(self.d.clock()).strftime(NOTE_STAMP)
        dest = outbox / f"{stamp}-from-{CODE}-ANSWER-re-{Path(name).stem}.md"
        if not self.write_note(dest, with_hop(ascii_text(reply), self.fi.next_hop(dec.hop))):
            return False
        to = dec.sender or "MAIN"
        self.cap.record(dest.name, "ANSWER", to)
        self.fi.mark_seen(self.root, name, dec, verdict="ANSWER", clock=self.d.clock)
        n_ok = int(self.deliver_note(dest, to))
        self.step(f"inbox answered: {name} -> {to} ({n_ok}/1 reached)"[:200])
        return True

    def orders(self):
        return queued_orders(self.root)

    def queue_order(self, oid, name, text):
        if self.dry or any(o["id"] == oid for o in self.orders()):
            return
        doc = {"orders": self.orders() + [{"id": oid, "note": name, "text": text,
                                           "title": ascii_text(name, 120)}]}
        atomic_write(self.root / ORDERS_REL, json.dumps(doc, indent=1))
        self.step(f"inbox escalated: {name} -> lane item {oid}")
