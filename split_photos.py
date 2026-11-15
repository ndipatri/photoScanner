#!/usr/bin/env python3
"""Split scanned photo-album pages into one image per photo.

Finds each photo on the page by colour (the album sleeve is nearly
colourless, photos are not), so it adapts when photos sit in slightly
different places from page to page. Slightly tilted photos are
straightened.

Usage:
    python3 split_photos.py [INPUT ...] [-o OUTPUT_DIR] [--watch]

With --watch it keeps running and splits each new scan as soon as it has
finished arriving in the folder.

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
import os
import shutil
import subprocess
import sys
import tempfile
import time
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
        res = subprocess.run(
            ["magick", "-density", str(DPI), f"{path}[0]",
             "-background", "white", "-alpha", "remove", str(png)],
            capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError("not a readable PDF (incomplete or damaged?)")
        path = png
    img = cv2.imread(str(path))
    if img is None:
        raise RuntimeError("not a readable image")
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


def process(f: Path, args, tmp: Path):
    """Split one scan into photos; move it to done/ unless --keep."""
    out = Path(args.out).expanduser() if args.out else f.parent / "photos"
    out.mkdir(parents=True, exist_ok=True)
    img = load_page(f, tmp)
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
    if args.keep:
        ledger_add(f)
    else:
        done = f.parent / "done"
        done.mkdir(exist_ok=True)
        target = unused_name(done, f.stem, f.suffix)
        shutil.move(str(f), str(target))
        moved = f", moved to done/{target.name}"
    print(f"{f.name}: {len(rects)} photo(s) -> {', '.join(names) or 'none'}{moved}",
          flush=True)
    if not rects:
        print("  (no photos found; try --debug to see why)", flush=True)


# With --keep, scans stay in place, so remember which ones were split in a
# small ledger file next to them (name, size and modification time).
LEDGER = ".split_photos_done"


def _ledger_key(f: Path) -> str:
    st = f.stat()
    return f"{f.name}\t{st.st_size}\t{int(st.st_mtime)}"


def ledger_has(f: Path) -> bool:
    ledger = f.parent / LEDGER
    return ledger.exists() and _ledger_key(f) in ledger.read_text().splitlines()


def ledger_add(f: Path):
    with open(f.parent / LEDGER, "a") as fh:
        fh.write(_ledger_key(f) + "\n")


def pending(inputs, keep):
    """Scans that still need splitting."""
    files = gather(inputs)
    return [f for f in files if not (keep and ledger_has(f))]


def out_dirs(args):
    """Folders the photos are written to."""
    if args.out:
        return [Path(args.out).expanduser()]
    dirs = []
    for p in args.inputs:
        p = Path(p).expanduser()
        d = (p if p.is_dir() else p.parent) / "photos"
        if d not in dirs:
            dirs.append(d)
    return dirs


def upload(args):
    """Upload any photos not yet in Google Photos. Raises on failure."""
    import google_photos
    for d in out_dirs(args):
        if d.is_dir():
            google_photos.upload_pending(d, args.album)


class Uploader:
    """Upload in watch mode, backing off after failures and printing each
    distinct error only once so the log doesn't fill up."""
    RETRY = [30, 60, 300, 900]

    def __init__(self, args):
        self.args, self.next_try, self.fails, self.last_err = args, 0.0, 0, None

    def tick(self):
        if not self.args.upload or time.time() < self.next_try:
            return
        try:
            upload(self.args)
            if self.last_err:
                print("Google Photos upload working again.", flush=True)
            self.fails, self.last_err, self.next_try = 0, None, 0.0
        except Exception as e:
            msg = str(e)
            if msg != self.last_err:
                print(f"Google Photos upload failed: {msg}", flush=True)
            self.last_err = msg
            self.next_try = time.time() + self.RETRY[min(self.fails, len(self.RETRY) - 1)]
            self.fails += 1


def watch(args, tmp: Path):
    """Keep checking the folder; split each new scan once it has finished
    arriving (same size and time on two checks in a row)."""
    last_seen = {}   # path -> (size, mtime) at previous check
    failed = {}      # path -> (size, mtime) when it last failed
    uploader = Uploader(args)
    where = ", ".join(str(Path(p).expanduser()) for p in args.inputs)
    extra = f', uploading to Google Photos album "{args.album}"' if args.upload else ""
    print(f"Watching {where} every {args.interval:g}s{extra}. Press Ctrl-C to stop.",
          flush=True)
    while True:
        uploader.tick()
        current = {}
        for f in pending(args.inputs, args.keep):
            try:
                st = f.stat()
            except FileNotFoundError:
                continue
            current[f] = (st.st_size, st.st_mtime)
        for f, sig in current.items():
            if sig[0] == 0 or last_seen.get(f) != sig or failed.get(f) == sig:
                continue  # empty, still being written, or already failed as-is
            try:
                process(f, args, tmp)
                failed.pop(f, None)
            except Exception as e:  # keep watching even if one scan is bad
                failed[f] = sig
                print(f"{f.name}: could not split ({e}); will retry if it changes",
                      flush=True)
        last_seen = current
        time.sleep(args.interval)


AGENT_LABEL = "com.photoscanner.watch"
AGENT_PLIST = Path.home() / "Library/LaunchAgents" / f"{AGENT_LABEL}.plist"
AGENT_LOG = Path.home() / "Library/Logs/photoScanner.log"


def _launchctl(*cmd):
    return subprocess.run(["launchctl", *cmd], capture_output=True, text=True)


def install_agent(args):
    """Install a macOS LaunchAgent that runs --watch at login and keeps it running."""
    import plistlib
    if sys.platform != "darwin":
        sys.exit("--install is for macOS. On Linux, run --watch from a systemd user service.")
    prog = [sys.executable, "-u", str(Path(__file__).resolve()),
            *[str(Path(p).expanduser().resolve()) for p in args.inputs],
            "--watch", "--interval", f"{args.interval:g}"]
    if args.out:
        prog += ["-o", str(Path(args.out).expanduser().resolve())]
    if args.keep:
        prog.append("--keep")
    if args.debug:
        prog.append("--debug")
    if args.upload:
        prog += ["--upload", "--album", args.album]
    # launchd starts with a bare PATH; include Homebrew so ImageMagick and
    # Ghostscript are found.
    path = ":".join(["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin",
                     "/usr/sbin", "/sbin"])
    plist = {
        "Label": AGENT_LABEL,
        "ProgramArguments": prog,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 30,
        "EnvironmentVariables": {"PATH": path},
        "StandardOutPath": str(AGENT_LOG),
        "StandardErrorPath": str(AGENT_LOG),
    }
    AGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
    AGENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    # Replace any old one. bootout returns before the old watcher has fully
    # stopped, and bootstrapping too soon fails with "Input/output error".
    _launchctl("bootout", f"{domain}/{AGENT_LABEL}")
    for _ in range(50):
        if _launchctl("print", f"{domain}/{AGENT_LABEL}").returncode != 0:
            break
        time.sleep(0.2)
    with open(AGENT_PLIST, "wb") as fh:
        plistlib.dump(plist, fh)
    res = _launchctl("bootstrap", domain, str(AGENT_PLIST))
    for _ in range(4):           # still racing on a slow machine: retry briefly
        if res.returncode == 0:
            break
        time.sleep(1)
        res = _launchctl("bootstrap", domain, str(AGENT_PLIST))
    if res.returncode != 0:
        sys.exit(f"launchctl could not start it: {res.stderr.strip()}")
    print(f"Installed. Watching {', '.join(prog[3:3 + len(args.inputs)])} "
          f"now and at every login.\nLog: {AGENT_LOG}\n"
          f"Stop and remove with: python3 {Path(__file__).name} --uninstall")


def uninstall_agent():
    _launchctl("bootout", f"gui/{os.getuid()}/{AGENT_LABEL}")
    if AGENT_PLIST.exists():
        AGENT_PLIST.unlink()
        print("Uninstalled; the watcher is stopped and won't start at login.")
    else:
        print("Nothing installed.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("inputs", nargs="*", default=["~/scans"])
    ap.add_argument("-o", "--out", help="output folder (default: <input folder>/photos)")
    ap.add_argument("--debug", action="store_true",
                    help="also save <name>_boxes.jpg showing what was detected")
    ap.add_argument("--keep", action="store_true",
                    help="don't move split scans into done/ (a ledger file "
                         "remembers which were split)")
    ap.add_argument("--watch", action="store_true",
                    help="keep running and split new scans as they arrive")
    ap.add_argument("--interval", type=float, default=5,
                    help="seconds between checks in --watch mode (default 5)")
    ap.add_argument("--install", action="store_true",
                    help="macOS: run --watch in the background now and at every login")
    ap.add_argument("--uninstall", action="store_true",
                    help="macOS: stop and remove the background watcher")
    ap.add_argument("--upload", action="store_true",
                    help="also upload new photos to Google Photos")
    ap.add_argument("--album", default="Photo Album Scans",
                    help='Google Photos album to upload into (default "Photo Album Scans")')
    ap.add_argument("--photos-login", action="store_true",
                    help="sign in to Google Photos (once, in your browser)")
    args = ap.parse_args()

    if args.photos_login:
        import google_photos
        return google_photos.login()
    if args.uninstall:
        return uninstall_agent()
    if args.install:
        if args.upload:
            import google_photos
            google_photos.credentials()   # fail now, not in the background
        return install_agent(args)

    with tempfile.TemporaryDirectory() as tmp:
        if args.watch:
            try:
                watch(args, Path(tmp))
            except KeyboardInterrupt:
                print("\nStopped watching.")
            return
        files = pending(args.inputs, args.keep)
        if not files:
            print("No new scans found.")
        for f in files:
            try:
                process(f, args, Path(tmp))
            except Exception as e:
                print(f"{f.name}: could not split ({e})")
        if args.upload:
            try:
                upload(args)
            except Exception as e:
                sys.exit(f"Google Photos upload failed: {e}")


if __name__ == "__main__":
    main()
