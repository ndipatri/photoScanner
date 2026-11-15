#!/usr/bin/env python3
"""Split scanned photo-album pages into one image per photo.

Finds each photo on the page by colour (the album sleeve is nearly
colourless, photos are not), so it adapts when photos sit in slightly
different places from page to page. Slightly tilted photos are
straightened.

Usage:
    python3 split_photos.py [INPUT ...] [-o OUTPUT_DIR]

INPUT can be PDFs, JPEG/PNG images, or folders of them
(default: ~/scans). Output goes to OUTPUT_DIR (default: <input folder>/photos),
named by the time the page was scanned, e.g. 2026-11-15_1527_1.jpg, _2, _3
from top to bottom. Existing photos are never overwritten.

After a scan is split it is moved to <its folder>/done/, so each run only
handles new scans. Use --keep to leave scans where they are.

Needs: ImageMagick + Ghostscript (for PDFs), and Python packages
numpy + opencv-python-headless.
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

DPI = 300                 # resolution PDFs are rendered at
DETECT_WIDTH = 1200       # pages are shrunk to this width to find photos
SAT_MIN = 25              # sleeve saturation is ~0-15; photos are higher
DARK_MAX = 150            # very dark pixels count as photo even if grey
MIN_AREA_FRAC = 0.02      # ignore blobs smaller than 2% of the page
MIN_FILL = 0.80           # blob must fill most of its rectangle
INSET_PX = 6              # trim this many px (full-res) off each edge
EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}


def load_page(path: Path, tmp: Path):
    """Return the page as a BGR image (first page only for PDFs)."""
    if path.suffix.lower() == ".pdf":
        png = tmp / (path.stem + ".png")
        subprocess.run(
            ["magick", "-density", str(DPI), f"{path}[0]",
             "-background", "white", "-alpha", "remove", str(png)],
            check=True)
        path = png
    img = cv2.imread(str(path))
    if img is None:
        raise RuntimeError(f"could not read {path}")
    return img


def find_photos(img):
    """Return rotated rectangles (cv2.minAreaRect) for each photo, in full-res coords."""
    scale = DETECT_WIDTH / img.shape[1]
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(cv2.medianBlur(small, 7), cv2.COLOR_BGR2HSV)
    mask = ((hsv[..., 1] > SAT_MIN) | (hsv[..., 2] < DARK_MAX)).astype(np.uint8) * 255
    # Opening breaks thin bridges across the sleeve gaps and removes specks;
    # closing fills small pale areas inside a photo (e.g. snow, sky).
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((15, 15), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    page_area = mask.shape[0] * mask.shape[1]
    rects = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < MIN_AREA_FRAC * page_area:
            continue
        (cx, cy), (w, h), ang = cv2.minAreaRect(c)
        if area / (w * h) < MIN_FILL:
            continue
        rects.append(((cx / scale, cy / scale), (w / scale, h / scale), ang))
    # Top-to-bottom, then left-to-right for photos side by side.
    row_tol = img.shape[0] * 0.05
    rects.sort(key=lambda r: (round(r[0][1] / row_tol), r[0][0]))
    return rects


def crop_rect(img, rect):
    """Cut out a rotated rectangle and return it straightened."""
    (cx, cy), (w, h), ang = rect
    # Keep the crop upright: minAreaRect may report the box turned by 90
    # degrees (angle near +/-90 with width and height swapped).
    while ang > 45:
        ang -= 90
        w, h = h, w
    while ang < -45:
        ang += 90
        w, h = h, w
    m = cv2.getRotationMatrix2D((cx, cy), ang, 1.0)
    rotated = cv2.warpAffine(img, m, (img.shape[1], img.shape[0]),
                             flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))
    x0 = int(round(cx - w / 2)) + INSET_PX
    y0 = int(round(cy - h / 2)) + INSET_PX
    x1 = int(round(cx + w / 2)) - INSET_PX
    y1 = int(round(cy + h / 2)) - INSET_PX
    return rotated[max(y0, 0):y1, max(x0, 0):x1]


def unused_name(folder: Path, stem: str, suffix: str) -> Path:
    """folder/stem+suffix, or stem-2, stem-3 ... if that name is taken."""
    p = folder / f"{stem}{suffix}"
    n = 2
    while p.exists():
        p = folder / f"{stem}-{n}{suffix}"
        n += 1
    return p


def scan_stamp(path: Path) -> str:
    """Scan time from the file's modification time, e.g. 2026-11-15_1527."""
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d_%H%M")


def gather(inputs):
    files = []
    for p in inputs:
        p = Path(p).expanduser()
        if p.is_dir():
            files += sorted(f for f in p.iterdir()
                            if f.suffix.lower() in EXTS and not f.name.startswith("."))
        elif p.suffix.lower() in EXTS:
            files.append(p)
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("inputs", nargs="*", default=["~/scans"])
    ap.add_argument("-o", "--out", help="output folder (default: <input folder>/photos)")
    ap.add_argument("--debug", action="store_true",
                    help="also save <name>_boxes.jpg showing what was detected")
    ap.add_argument("--keep", action="store_true",
                    help="don't move split scans into the done/ folder")
    args = ap.parse_args()

    files = gather(args.inputs)
    if not files:
        sys.exit("No new scans found.")

    with tempfile.TemporaryDirectory() as tmp:
        for f in files:
            out = Path(args.out).expanduser() if args.out else f.parent / "photos"
            out.mkdir(parents=True, exist_ok=True)
            img = load_page(f, Path(tmp))
            rects = find_photos(img)
            stamp = scan_stamp(f)
            names = []
            for i, r in enumerate(rects, 1):
                dest = unused_name(out, f"{stamp}_{i}", ".jpg")
                cv2.imwrite(str(dest), crop_rect(img, r), [cv2.IMWRITE_JPEG_QUALITY, 95])
                names.append(dest.name)
            if args.debug:
                dbg = img.copy()
                for r in rects:
                    box = cv2.boxPoints(r).astype(np.int32)
                    cv2.drawContours(dbg, [box], 0, (0, 0, 255), 8)
                cv2.imwrite(str(unused_name(out, f"{stamp}_boxes", ".jpg")), dbg)
            moved = ""
            if not args.keep:
                done = f.parent / "done"
                done.mkdir(exist_ok=True)
                target = unused_name(done, f.stem, f.suffix)
                shutil.move(str(f), str(target))
                moved = f", moved to done/{target.name}"
            print(f"{f.name}: {len(rects)} photo(s) -> {', '.join(names) or 'none'}{moved}")
            if not rects:
                print("  (no photos found; try --debug to see why)")


if __name__ == "__main__":
    main()
