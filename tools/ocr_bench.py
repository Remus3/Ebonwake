#!/usr/bin/env python3
"""Synthetic OCR benchmark for BDO-style silver / number strings (plan 009 follow-up).

    python tools/ocr_bench.py [--out DIR] [--quick] [--json REPORT]

Renders BDO-like strings ("Silver 1,234,567", bare comma-grouped amounts,
"Silver: 98,765") in several UI fonts at in-game pixel sizes (11-28 px), light on
dark (the game's look) and dark on light, as tight crops and inside a 1920x1080
frame with distractor text. Each image is read by every engine configuration
and scored with the production extractor (`server.ew.ocr.extract_silver` /
`_amount`): a case passes only when the full number comes back exactly.

Engines: Windows.Media.Ocr (as is and upscaled) and Tesseract (found through
`server.ew.ocr.tesseract_exe`, preprocessed: grayscale, inverted when dark,
upscaled). Synthetic renders only; no game file, window or input is touched.
"""

import argparse
import json
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from server.ew import ocr  # noqa: E402

PS1 = ROOT / "tools" / "ocr_bench.ps1"
FONTS = ("Arial", "Segoe UI", "Tahoma", "Malgun Gothic")
SIZES = (11, 12, 13, 14, 16, 18, 22, 28)
STYLES = (("#e8dcc0", "#15161a"), ("#101010", "#f4f4f4"))  # (fg, bg)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ps(*args, timeout=3600):
    cmd = [ocr.powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
           "Bypass", "-File", str(PS1), *map(str, args)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"ocr_bench.ps1 {args[0]} failed: {r.stderr[-400:]}")
    return r.stdout


def _amount_text(rng):
    digits = rng.choice((4, 5, 6, 7, 7, 8, 9, 10, 11, 12))
    n = rng.randrange(10 ** (digits - 1), 10 ** digits)
    return n, f"{n:,}"


def cases(quick=False, seed=9):
    rng = random.Random(seed)
    out = []
    sizes = SIZES[::2] if quick else SIZES
    for font in FONTS:
        for px in sizes:
            for si, (fg, bg) in enumerate(STYLES):
                for kind in ("silver", "bare", "colon"):
                    for frame in (0, 1):
                        if frame and si:  # frames are game-like: dark only
                            continue
                        n, s = _amount_text(rng)
                        if kind == "silver" and len(out) % 7 == 0:
                            n, s = 1234567, "1,234,567"  # the verifier's case
                        text = {"silver": f"Silver {s}", "bare": s,
                                "colon": f"Silver: {s}"}[kind]
                        out.append({"id": f"c{len(out):04d}", "text": text, "want": n,
                                    "kind": kind, "font": font, "px": px, "fg": fg,
                                    "bg": bg, "frame": frame})
    return out


def score(case, doc):
    """True when the production extractor returns the exact number."""
    lines = ocr._lines(doc.get("lines"))
    if case["kind"] == "bare":
        for ln in lines:
            m = ocr._amount(ln["text"])
            if m == case["want"]:
                return True
        return False
    return ocr.extract_silver(lines) == case["want"]


def run_winocr(paths, scale, work):
    lst = work / f"win_{scale}.txt"
    lst.write_text("\n".join(str(p) for p in paths) + "\n", encoding="ascii")
    docs = {}
    for line in _ps("winocr", lst, scale).splitlines():
        if line.strip():
            d = json.loads(line)
            docs[Path(d["path"]).name] = d
    return docs


def prep(paths, scale, work):
    lst = work / "prep_list.txt"
    lst.write_text("\n".join(str(p) for p in paths) + "\n", encoding="ascii")
    outdir = work / f"prep_{scale}"
    _ps("prep", lst, f"{scale},{outdir}")
    return outdir


def run_tesseract(paths, scale, psm, prepdir, exe):
    docs = {}
    for p in paths:
        img = prepdir / Path(p).name
        docs[Path(p).name] = ocr.run_tesseract(img, exe=exe, psm=psm, scale=scale)
    return docs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="work dir (default: a temp dir)")
    ap.add_argument("--quick", action="store_true", help="half the font sizes")
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--rescore", action="store_true",
                    help="re-score the raw reads already in --out (no OCR)")
    a = ap.parse_args(argv)
    work = Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix="ew_ocr_bench_"))
    work.mkdir(parents=True, exist_ok=True)
    if a.rescore:
        cs = json.loads((work / "manifest.json").read_text(encoding="ascii"))
        configs = {p.stem[4:].replace("_", " "): (json.loads(p.read_text(encoding="ascii")), 0)
                   for p in sorted(work.glob("raw_*.json"))}
        return report_out(cs, configs, a.json)
    cs = cases(a.quick)
    (work / "manifest.json").write_text(json.dumps(cs), encoding="ascii")
    _ps("render", work / "manifest.json", work / "img")
    paths = [work / "img" / f"{c['id']}.png" for c in cs]

    configs = {}
    t = time.time()
    configs["win x1"] = (run_winocr(paths, 1, work), time.time() - t)
    t = time.time()
    configs["win x2"] = (run_winocr(paths, 2, work), time.time() - t)
    exe = ocr.tesseract_exe()
    if exe:
        for scale in (1, 3):
            pd = prep(paths, scale, work)
            for psm in (6, 11):
                t = time.time()
                configs[f"tess x{scale} psm{psm}"] = (
                    run_tesseract(paths, scale, psm, pd, exe), time.time() - t)
    else:
        print("tesseract: not found", file=sys.stderr)

    for name, (docs, _) in configs.items():  # raw reads, for re-scoring / audit
        (work / f"raw_{name.replace(' ', '_')}.json").write_text(json.dumps(docs), encoding="ascii")
    return report_out(cs, configs, a.json)


def chain_reads(cs, configs):
    """The production chain (ocr.TESS_PASSES) replayed from the raw pass reads:
    first pass whose read yields an amount, else the first pass."""
    names = [f"tess x{int(sc)} psm{psm}" for sc, psm in ocr.TESS_PASSES]
    if not all(n in configs for n in names):
        return None
    docs = {}
    for c in cs:
        key = f"{c['id']}.png"
        reads = [configs[n][0].get(key) or {"lines": []} for n in names]
        pick = reads[0]
        for d in reads:
            lines = ocr._lines(d.get("lines"))
            got = ocr.extract_silver(lines) if c["kind"] != "bare" else next(
                (v for v in (ocr._amount(ln["text"]) for ln in lines) if v is not None), None)
            if got is not None:
                pick = d
                break
        docs[key] = pick
    return docs, sum(configs[n][1] for n in names)


def report_out(cs, configs, json_path):
    chain = chain_reads(cs, configs)
    if chain:
        configs = dict(configs, chain=chain)
    report = {"cases": len(cs), "configs": {}}
    for name, (docs, secs) in configs.items():
        res = {c["id"]: score(c, docs.get(f"{c['id']}.png") or {"lines": []}) for c in cs}
        groups = {}
        for c in cs:
            for key in (f"px{c['px']:02d}", c["kind"], "frame" if c["frame"] else "crop",
                        "dark" if c["bg"] == STYLES[0][1] else "light", c["font"]):
                ok, n = groups.get(key, (0, 0))
                groups[key] = (ok + res[c["id"]], n + 1)
        report["configs"][name] = {
            "pass": sum(res.values()), "secs": round(secs, 1),
            "per_image_ms": round(1000 * secs / max(1, len(cs))),
            "groups": {k: f"{ok}/{n}" for k, (ok, n) in sorted(groups.items())},
            "fails": [c["id"] for c in cs if not res[c["id"]]],
        }
    for name, r in report["configs"].items():
        print(f"{name:18s} {r['pass']:4d}/{len(cs)}  {r['per_image_ms']:5d} ms/img")
    if json_path:
        Path(json_path).write_text(json.dumps(report, indent=1), encoding="ascii")
    return 0


if __name__ == "__main__":
    sys.exit(main())
