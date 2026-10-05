"""FLEET-COMMON item 14 (kit v8) inbox rules: tools/ew_inbox.py."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ew_inbox as ib  # noqa: E402

NOW = 1_790_000_000.0


def test_classify():
    assert ib.classify("x-from-EW-QUESTION-a.md", "EW") == "skip"
    assert ib.classify("x-from-MAIN-INFORMATION-TERMINAL-a.md", "EW") == "skip"
    assert ib.classify("x-from-MAIN-STATUS-a.md", "EW", "# From MAIN\nNO-REPLY\n") == "skip"
    for cls in ("ACK", "ANSWER", "INFORMATION"):
        assert ib.classify(f"x-from-MAIN-{cls}-a.md", "EW") == "ack"
    for cls in ("ORDER", "FIX", "RULING"):
        assert ib.classify(f"x-from-MAIN-{cls}-a.md", "EW", "TERMINAL\nHOP: 5") == "work"
    assert ib.classify("x-from-MAIN-QUESTION-a.md", "EW", "HOP: 2\n") == "ack"
    assert ib.classify("x-from-MAIN-QUESTION-a.md", "EW", "# From MAIN\nq\n") == "triage"
    assert ib.classify("x.md", "EW", "# From MAIN - QUESTION\nq\n") == "triage"


def test_hop():
    assert ib.hop("no line") == 1 and ib.next_hop("no line") == 2
    assert ib.hop("# t\nHOP: 3\n") == 3
    assert ib.may_reply("") and not ib.may_reply("HOP: 2")
    assert ib.may_reply("HOP: 4", "ORDER") and not ib.may_reply("", "ANSWER")
    assert ib.with_hop("# From EW - ANSWER\nHOP: 9\nbody", 2) == "# From EW - ANSWER\nHOP: 2\nbody\n"
    assert ib.with_hop("body", 2) == "HOP: 2\nbody\n"


def test_parse_verdict():
    assert ib.parse_verdict("VERDICT: NOREPLY\nwhatever") == ("NOREPLY", "")
    assert ib.parse_verdict("verdict: ack") == ("ACK", "")
    assert ib.parse_verdict("VERDICT: ANSWER\n# From EW\nx") == ("ANSWER", "# From EW\nx")
    assert ib.parse_verdict("plain reply") == ("ANSWER", "plain reply")
    assert ib.parse_verdict("") == ("NOREPLY", "")
    assert ib.TRIAGE_SPAWN == {"model": "sonnet", "effort": "low", "bare": True}


def test_outbound_cap_six_orders_exempt(tmp_path):
    cap = ib.OutboundCap(tmp_path, 99)
    assert cap.cap == 6
    for i in range(6):
        assert cap.allow("ANSWER", NOW)
        cap.record("ANSWER", "MAIN", f"n{i}", NOW)
    assert not cap.allow("ANSWER", NOW) and cap.allow("ORDER", NOW)
    cap.record("ANSWER", "MAIN", "order-close", NOW, exempt=True)
    assert cap.used(NOW) == 6
    assert cap.allow("ANSWER", NOW + 86400)  # a new local day
    assert not ib.OutboundCap(tmp_path, 2).allow("ANSWER", NOW + 86400, pending=2)


def test_batch_note():
    text = ib.batch_note("EW", "MAIN", [("a.md", "# From EW - ANSWER re a\nHOP: 2\none"),
                                        ("b.md", "two")])
    assert text.startswith("# From EW - ANSWER to MAIN (batch of 2)\nHOP: 2\n\n## re a.md\n")
    assert "one" in text and "## re b.md\n\ntwo" in text and text.count("HOP:") == 1


def test_ledger(tmp_path):
    led = ib.Ledger(tmp_path)
    led.mark("a.md", "ack", NOW)
    assert not led.answered("a.md")
    led.mark("a.md", "answered", NOW)
    assert led.answered("a.md") and not led.answered("b.md")


def test_with_kind_v6_drops_v8_keeps():
    def v6(root, code, prompt, note="", governor=None):
        return None

    def v8(root, code, prompt, note="", kind="build"):
        return None

    assert ib.with_kind(v6, {"note": "n"}, "triage") == {"note": "n"}
    assert ib.with_kind(v8, {"note": "n"}, "triage") == {"note": "n", "kind": "triage"}
    assert ib.with_kind(lambda *a, **k: None, {}, "inbox") == {"kind": "inbox"}


def test_with_kind_on_vendored_kit_spawn():
    import ew_lane
    kit = ew_lane.kit()
    got = ib.with_kind(kit.spawn, {"note": "n"}, "build")
    assert ("kind" in got) == (kit.KIT_VERSION >= 8)
