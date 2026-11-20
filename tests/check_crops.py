#!/usr/bin/env python3
"""Regression tests for split_photos.py, run on real album pages.

tests/reference/ holds scans from each kind of album seen so far, and
tests/expected.json says how each one must come out. Two checks per page:

  1. Marked photos ("truth"). Where a person marked where each photo really
     is (page pixels, four corners), every photo is graded:
         good   - a crop matches it (overlap >= GOOD)
         report - no crop touches it (it's left for a problem report)
         bad    - a crop only partly matches it (a wrong photo was uploaded)
     A photo that gets worse than its recorded grade (good -> report/bad,
     report -> bad) FAILS. A photo that gets better is reported as such.

  2. Exact results ("golden"). Every page's crops, problem-report kinds and
     upright turns must match what was recorded. Any change FAILS, so no
     page can change without someone looking at it.

Usage:
  python3 tests/check_crops.py              run everything
  python3 tests/check_crops.py 84 62        only these scans
  python3 tests/check_crops.py --show DIR   also save each page with its crops drawn
  python3 tests/check_crops.py --update     accept the current results (after
                                            checking them!) as the new expectations

Adding a page: copy the scan to tests/reference/, add an entry to
expected.json with "album" and "note" (and "truth" if you marked its photos),
then run with --update NAME to record its results.
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import split_photos as sp  # noqa: E402

EXPECTED = HERE / "expected.json"
REFERENCE = HERE / "reference"
GOOD, BAD = 0.80, 0.20         # overlap (IoU) grades for marked photos
SAME = 0.95                    # golden crops must overlap their record this well
RANK = {"bad": 0, "report": 1, "good": 2}


def iou(p, q, shape):
    """Overlap of two page-pixel quads, rasterized at 1/8 scale."""
    h, w = shape[0] // 8 + 2, shape[1] // 8 + 2
    a, b = np.zeros((h, w), np.uint8), np.zeros((h, w), np.uint8)
    cv2.fillPoly(a, [np.int32(np.float32(p) / 8)], 1)
    cv2.fillPoly(b, [np.int32(np.float32(q) / 8)], 1)
    return float((a & b).sum()) / max(float((a | b).sum()), 1.0)


def run_page(path, tmp):
    """What split_photos does with one scan: crops, report kinds, turns."""
    img = sp.load_page(path, tmp)
    missed = []
    rects = sp.find_photos(img, missed)
    cutoff = set(sp.cut_off(rects, img.shape))
    crops = []
    for i, r in enumerate(rects):
        turn = sp.upright_turn(sp.crop_rect(img, r))
        crops.append({"quad": np.round(cv2.boxPoints(r)).astype(int).tolist(),
                      "turn": int(turn), "cut_off": i in cutoff})
    return img.shape, crops, sorted(m["kind"] for m in missed)


def grade(quad, crops, shape):
    best = max((iou(quad, c["quad"], shape) for c in crops if not c["cut_off"]), default=0.0)
    return ("good" if best >= GOOD else "bad" if best > BAD else "report"), best


def golden_diff(rec, crops, missed, shape):
    """Differences between the recorded and current results, as text."""
    out = []
    if rec.get("missed", []) != missed:
        out.append(f"problem reports {rec.get('missed', [])} -> {missed}")
    old = rec.get("crops", [])
    used = set()
    for c in crops:
        match = max(((iou(c["quad"], o["quad"], shape), j) for j, o in enumerate(old) if j not in used),
                    default=(0.0, None))
        if match[0] < SAME:
            out.append(f"new or moved crop at {c['quad'][0]}..{c['quad'][2]}")
            continue
        used.add(match[1])
        o = old[match[1]]
        if o["turn"] != c["turn"] or o["cut_off"] != c["cut_off"]:
            out.append(f"crop at {c['quad'][0]}: turn {o['turn']}->{c['turn']}, "
                       f"cut off {o['cut_off']}->{c['cut_off']}")
    for j, o in enumerate(old):
        if j not in used:
            out.append(f"crop at {o['quad'][0]}..{o['quad'][2]} is gone")
    return out


def draw(path, crops, truth, out_dir):
    img = sp.load_page(path, Path(tempfile.mkdtemp()))
    for t in truth:
        cv2.polylines(img, [np.int32(t["quad"])], True, (255, 0, 0), 12)
    for c in crops:
        cv2.polylines(img, [np.int32(c["quad"])], True, (0, 0, 255) if c["cut_off"] else (0, 200, 0), 18)
    s = 1400 / img.shape[0]
    cv2.imwrite(str(Path(out_dir) / (path.stem + ".jpg")), cv2.resize(img, None, fx=s, fy=s))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("only", nargs="*", help="scan names or numbers to run (default: all)")
    ap.add_argument("--update", action="store_true", help="record the current results as expected")
    ap.add_argument("--show", metavar="DIR", help="save each page with crops (green), cut-off "
                    "crops (red) and marked photos (blue) drawn on it")
    args = ap.parse_args()

    expected = json.loads(EXPECTED.read_text())
    names = [n for n in expected if not args.only or any(
        n == o or n.split("-")[-1] == o.split("-")[-1] for o in args.only)]
    if args.show:
        Path(args.show).mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    failures, better, totals = [], [], {"good": 0, "report": 0, "bad": 0}
    t0 = time.time()
    for name in names:
        rec = expected[name]
        path = REFERENCE / f"{name}.pdf"
        if not path.exists():
            failures.append(f"{name}: missing {path}")
            continue
        shape, crops, missed = run_page(path, tmp)
        notes = []
        for k, t in enumerate(rec.get("truth", [])):
            g, best = grade(t["quad"], crops, shape)
            totals[g] += 1
            want = t.get("grade", g)
            label = t.get("label", f"photo {k + 1}")
            if RANK[g] < RANK[want]:
                notes.append(f"FAIL {label}: {want} -> {g} (overlap {best:.2f})")
            elif RANK[g] > RANK[want]:
                better.append(f"{name} {label}: {want} -> {g} (overlap {best:.2f})")
            if args.update:
                t["grade"] = g
        diff = golden_diff(rec, crops, missed, shape)
        if args.update:
            rec["crops"], rec["missed"] = crops, missed
        elif diff:
            notes += ["CHANGED " + d for d in diff]
        status = "ok" if not notes or args.update else "FAIL"
        print(f"{name:9s} {status:4s} {rec.get('album', '')}: {len(crops)} crop(s), reports {missed}")
        for n in notes:
            print("          " + n)
        if notes and not args.update:
            failures.append(name)
        if args.show:
            draw(path, crops, rec.get("truth", []), args.show)

    print(f"\nMarked photos: {totals['good']} good, {totals['report']} left for a report, "
          f"{totals['bad']} bad crops. {len(names)} page(s) in {time.time() - t0:.0f}s.")
    for b in better:
        print("Improved: " + b)
    if args.update:
        EXPECTED.write_text(json.dumps(expected, indent=1) + "\n")
        print(f"Recorded the current results in {EXPECTED.name}.")
        return 0
    if failures:
        print(f"\nFAILED: {', '.join(failures)}")
        print("If a change is intended, look at it (--show DIR), then run --update.")
        return 1
    if better:
        print("Some marked photos improved: run --update to lock that in.")
    print("All pages as expected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
