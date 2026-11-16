#!/usr/bin/env python3
"""Split scanned photo-album pages into one image per photo.

Finds each photo on the page by colour (the album sleeve is nearly
colourless, photos are not), so it adapts when photos sit in slightly
different places from page to page. Slightly tilted photos are
straightened, and photos stored sideways or upside down in the album are
turned upright when they show a face. Dark photos are brightened.

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
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

DPI = 600                 # fallback resolution for PDFs that must be rendered
MIN_DPI = 300             # lower-resolution scans are enlarged to this first
DETECT_WIDTH = 1200       # pages are shrunk to this width to find photos
SAT_MIN = 25              # sleeve saturation is ~0-15; photos are higher
DARK_MAX = 150            # very dark pixels count as photo even if grey
MIN_AREA_FRAC = 0.02      # ignore blobs smaller than 2% of the page
MIN_FILL = 0.80           # blob must fill most of its rectangle
INSET_IN = 0.02           # trim this much (inches) off each edge of a photo
EXTS = {".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"}


page_dpi = DPI             # resolution of the page loaded last (set by load_page)


def _pdf_jpeg(path: Path):
    """The scanner's own image, when the PDF is just one JPEG (as HP scans are).

    Using it directly keeps the full scan resolution and avoids decoding,
    resampling and re-rendering the page. Returns (image, dpi) or None if the
    PDF is anything more complicated, in which case it is rendered instead.
    """
    d = path.read_bytes()
    if (len(re.findall(rb"/Subtype\s*/Image", d)) != 1 or d.count(b"/DCTDecode") != 1
            or b"/DeviceCMYK" in d or re.search(rb"/Rotate\s+[1-9]", d)
            or len(re.findall(rb"/Type\s*/Page[^s]", d)) != 1):
        return None
    m = re.search(rb"/DCTDecode.*?stream\r?\n", d, re.S)
    end = d.find(b"endstream", m.end()) if m else -1
    if end < 0 or d[m.end():m.end() + 2] != b"\xff\xd8":
        return None
    img = cv2.imdecode(np.frombuffer(d[m.end():end], np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    box = re.search(rb"/MediaBox\s*\[\s*[-\d.]+\s+[-\d.]+\s+([-\d.]+)\s+([-\d.]+)", d)
    width_in = float(box.group(1)) / 72 if box else 0
    return img, round(img.shape[1] / width_in) if width_in > 0 else DPI


def _image_dpi(path: Path):
    res = subprocess.run(["magick", "identify", "-units", "PixelsPerInch",
                          "-format", "%x", f"{path}[0]"], capture_output=True, text=True)
    try:
        dpi = round(float(res.stdout.split()[0]))
    except (ValueError, IndexError):
        return DPI
    return dpi if dpi >= 100 else DPI   # 72 means "unknown" in most files


def load_page(path: Path, tmp: Path):
    """Return the page as a BGR image at full scan resolution (first page only)."""
    global page_dpi
    if path.suffix.lower() == ".pdf":
        found = _pdf_jpeg(path)
        if found:
            img, page_dpi = found
        else:
            png = tmp / (path.stem + ".png")
            res = subprocess.run(
                ["magick", "-density", str(DPI), f"{path}[0]",
                 "-background", "white", "-alpha", "remove", str(png)],
                capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError("not a readable PDF (incomplete or damaged?)")
            img, page_dpi = cv2.imread(str(png)), DPI
    else:
        img, page_dpi = cv2.imread(str(path)), _image_dpi(path)
    if img is None:
        raise RuntimeError("not a readable image")
    if page_dpi < MIN_DPI:
        # Detection and face finding were tuned on low-resolution scans after
        # Ghostscript had enlarged them to 300 dpi, so enlarge them the same
        # way (plain pixel repetition). This adds no real detail.
        f = MIN_DPI / page_dpi
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_NEAREST)
        page_dpi = MIN_DPI
    return img


EDGE_T = 10               # gradient strength that counts as a photo's border
EDGE_SUPPORT = 0.7        # fraction of a side that must lie on that border
MAX_RECOVERED_FRAC = 0.35 # a recovered "photo" bigger than this is suspect
SLIVER_FRAC = 0.005       # smaller blobs at the scan's edge are ignored entirely
CUTOFF_RATIO = 0.6        # a photo at the scan edge this much smaller than the
                          # page's other photos is treated as cut off
_K15 = np.ones((15, 15), np.uint8)


def _mask(hsv, sat_min=SAT_MIN, dark_max=DARK_MAX):
    """Pixels that look like photo (coloured or dark), cleaned up. Opening
    breaks thin bridges across sleeve gaps and removes specks; closing fills
    small pale areas inside a photo."""
    m = ((hsv[..., 1] > sat_min) | (hsv[..., 2] < dark_max)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, _K15)
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE, _K15)


def _fill(c):
    (_, _), (w, h), _ = cv2.minAreaRect(c)
    return cv2.contourArea(c) / max(w * h, 1)


def _upright(rect):
    """Same rectangle with its angle in [-45, 45] (width/height swapped to match)."""
    (cx, cy), (w, h), a = rect
    while a > 45:
        a, w, h = a - 90, h, w
    while a < -45:
        a, w, h = a + 90, h, w
    return (cx, cy), (w, h), a


def _snap_to_edges(gray, rect):
    """Move each side of `rect` outward to the nearest long straight edge (the
    photo's border). Returns the snapped rect, or None if a side has no edge
    within one photo-length. Recovers photos with pale sky/snow along a side,
    where colour alone can't tell photo from sleeve."""
    (cx, cy), (w, h), a = _upright(rect)
    M = cv2.getRotationMatrix2D((cx, cy), a, 1.0)
    g = cv2.warpAffine(gray, M, gray.shape[::-1], flags=cv2.INTER_LINEAR, borderValue=255)
    g = cv2.GaussianBlur(g, (3, 3), 0).astype(np.float32)
    gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)) / 4
    gx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)) / 4
    H, W = g.shape
    x0, x1, y0, y1 = cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2

    def row(y, xa, xb):
        y, xa, xb = int(round(y)), int(max(xa, 0)), int(min(xb, W - 1))
        if y < 2 or y > H - 3 or xb <= xa:
            return 0.0
        return float((gy[y - 2:y + 3, xa:xb].max(axis=0) > EDGE_T).mean())

    def col(x, ya, yb):
        x, ya, yb = int(round(x)), int(max(ya, 0)), int(min(yb, H - 1))
        if x < 2 or x > W - 3 or yb <= ya:
            return 0.0
        return float((gx[ya:yb, x - 2:x + 3].max(axis=1) > EDGE_T).mean())

    def first_edge(start, step, limit, support):
        for d in range(int(limit)):
            if support(start + step * d) >= EDGE_SUPPORT:
                return start + step * d
        return None

    for _ in range(2):   # a moved side changes the span the others are measured on
        mx, my = (x1 - x0) * 0.1, (y1 - y0) * 0.1   # ignore the corners
        t = first_edge(y0, -1, y1 - y0, lambda v: row(v, x0 + mx, x1 - mx))
        b = first_edge(y1, +1, y1 - y0, lambda v: row(v, x0 + mx, x1 - mx))
        l = first_edge(x0, -1, x1 - x0, lambda v: col(v, y0 + my, y1 - my))
        r = first_edge(x1, +1, x1 - x0, lambda v: col(v, y0 + my, y1 - my))
        if None in (t, b, l, r):
            return None
        x0, x1, y0, y1 = l, r, t, b
    c = cv2.invertAffineTransform(M) @ np.array([(x0 + x1) / 2, (y0 + y1) / 2, 1.0])
    return (float(c[0]), float(c[1])), (x1 - x0, y1 - y0), a


def _recover(hsv, gray, c, page_area):
    """Try to find the photo inside a blob that isn't rectangular enough.
    Returns a rect (detection coords) or None."""
    region = np.zeros(hsv.shape[:2], np.uint8)
    cv2.drawContours(region, [c], 0, 255, -1)
    region = cv2.dilate(region, _K15)
    area0 = cv2.contourArea(c)

    # 1. The photo merged with the sleeve's yellowed edge: stricter colour
    #    thresholds drop the pale-yellow plastic and keep the photo.
    for sat in (40, 55, 70, 90):
        for dark in (DARK_MAX, 120):
            cs, _ = cv2.findContours(_mask(hsv, sat, dark) & region,
                                     cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if cs:
                best = max(cs, key=cv2.contourArea)
                a = cv2.contourArea(best)
                if a >= MIN_AREA_FRAC * page_area and a >= 0.5 * area0 and _fill(best) >= 0.88:
                    return cv2.minAreaRect(best)

    # 2. Pale sky/snow along a side: snap the box to the photo's border.
    r = _snap_to_edges(gray, cv2.minAreaRect(cv2.convexHull(c)))
    if r and r[1][0] * r[1][1] >= MIN_AREA_FRAC * page_area:
        return r

    # 3. A small pale notch: the convex hull is still a clean rectangle.
    hull = cv2.convexHull(c)
    r = cv2.minAreaRect(hull)
    ra = r[1][0] * r[1][1]
    if cv2.contourArea(hull) / ra >= 0.95 and area0 / ra >= 0.6:
        return r

    # 4. A thin attachment: a much larger opening cuts it off.
    for k in (31, 51):
        m = cv2.morphologyEx(region & _mask(hsv), cv2.MORPH_OPEN, np.ones((k, k), np.uint8))
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if cs:
            best = max(cs, key=cv2.contourArea)
            if cv2.contourArea(best) >= MIN_AREA_FRAC * page_area and _fill(best) >= 0.88:
                return cv2.minAreaRect(best)
    return None


def _touches_edge(rect, shape, margin=0.01):
    """Does the rotated rect reach within `margin` of the image border?"""
    pts = cv2.boxPoints(rect)
    h, w = shape[:2]
    return bool(pts[:, 0].min() < w * margin or pts[:, 1].min() < h * margin
                or pts[:, 0].max() > w * (1 - margin) or pts[:, 1].max() > h * (1 - margin))


def cut_off(rects, shape):
    """Indexes of photos that touch the scan's edge and are much smaller than
    the page's other photos, i.e. probably partly off the scanner."""
    areas = [r[1][0] * r[1][1] for r in rects]
    out = []
    for i, r in enumerate(rects):
        others = areas[:i] + areas[i + 1:]
        if others and _touches_edge(r, shape) and areas[i] < CUTOFF_RATIO * float(np.median(others)):
            out.append(i)
    return out


def _looks_like_photo_piece(hsv, c, shape):
    """Is this small blob at the scan's edge a piece of a photo, rather than a
    yellowed sleeve corner or a thin strip of the album page's edge?"""
    (_, _), (w, h), _ = cv2.minAreaRect(c)
    if min(w, h) < 0.015 * shape[1] or _fill(c) < 0.75:
        return False
    m = np.zeros(shape[:2], np.uint8)
    cv2.drawContours(m, [c], 0, 255, -1)
    sat, val = hsv[..., 1][m > 0], hsv[..., 2][m > 0]
    return bool(np.median(sat) >= 60 or np.median(val) <= 120)


def _overlap(r1, r2):
    ret, pts = cv2.rotatedRectangleIntersection(r1, r2)
    return cv2.contourArea(pts) if ret != cv2.INTERSECT_NONE and pts is not None else 0.0


def find_photos(img, missed=None, recovered=None):
    """Return rotated rectangles (cv2.minAreaRect) for each photo, in full-res coords.

    Blobs that aren't rectangular enough (a photo merged with the sleeve edge,
    or with pale sky/snow along a side) go through _recover(). If `recovered`
    is a list, the indexes (in the returned order) of photos found that way are
    stored in it. If `missed` is a list, photo-sized regions that still couldn't
    be extracted are appended as dicts (rect, area_frac, fill) for reporting."""
    scale = DETECT_WIDTH / img.shape[1]
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(cv2.medianBlur(small, 7), cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    mask = _mask(hsv)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    page_area = mask.shape[0] * mask.shape[1]
    good, fixed, lost, slivers = [], [], [], []
    for c in contours:
        area = cv2.contourArea(c)
        if area < MIN_AREA_FRAC * page_area:
            # A photo lying mostly off the scanner leaves a small piece at the
            # edge of the scan; report it rather than ignoring it.
            if area >= SLIVER_FRAC * page_area and _touches_edge(cv2.minAreaRect(c), mask.shape) \
                    and _looks_like_photo_piece(hsv, c, mask.shape):
                slivers.append(c)
            continue
        if _fill(c) >= MIN_FILL:
            good.append(cv2.minAreaRect(c))
            continue
        r = _recover(hsv, gray, c, page_area)
        if r:
            fixed.append((r, c))
        else:
            lost.append(c)

    # A recovered photo must be photo-sized and must not overlap a photo found
    # normally; otherwise it probably swallowed its neighbours.
    for r, c in fixed:
        ra = r[1][0] * r[1][1]
        if ra > MAX_RECOVERED_FRAC * page_area or any(
                _overlap(r, o) > 0.05 * min(ra, o[1][0] * o[1][1]) for o in good):
            lost.append(c)
        else:
            good.append(r)
    fixed_ids = {id(r) for r, _ in fixed}

    def to_full(r):
        (cx, cy), (w, h), ang = r
        return (cx / scale, cy / scale), (w / scale, h / scale), ang

    row_tol = img.shape[0] * 0.05
    key = lambda r: (round(r[0][1] / row_tol), r[0][0])
    pairs = sorted(((to_full(r), id(r) in fixed_ids) for r in good), key=lambda p: key(p[0]))
    rects = [r for r, _ in pairs]
    if recovered is not None:
        recovered.extend(i for i, (_, was_fixed) in enumerate(pairs) if was_fixed)
    if missed is not None:
        for kind, cs in (("irregular", lost), ("off-scanner", slivers)):
            for c in cs:
                missed.append({"rect": to_full(cv2.minAreaRect(c)), "kind": kind,
                               "area_frac": cv2.contourArea(c) / page_area, "fill": _fill(c)})
        missed.sort(key=lambda m: key(m["rect"]))
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
    inset = int(round(INSET_IN * page_dpi))
    x0 = int(round(cx - w / 2)) + inset
    y0 = int(round(cy - h / 2)) + inset
    x1 = int(round(cx + w / 2)) - inset
    y1 = int(round(cy + h / 2)) - inset
    return rotated[max(y0, 0):y1, max(x0, 0):x1]


# ---- turning sideways photos upright ----------------------------------------
# Photos are often stored sideways in an album. We try each quarter turn and
# keep the one where a face detector (OpenCV's YuNet, a small model that runs
# locally) finds upright faces. Photos without a clear face are left alone.
FACE_MODEL = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"
FACE_SIZE = 960           # photos are shrunk to this long side for face finding
FACE_MIN_SCORE = 0.75     # ignore weaker face detections
TURN_MIN_SCORE = 0.80     # a turn needs a face at least this confident...
TURN_SURE_SCORE = 0.88    # ...and this confident if another turn also shows one
_TURNS = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
          270: cv2.ROTATE_90_COUNTERCLOCKWISE}
_face_detector = None


def _faces_upright_score(det, img):
    """Sum of confidences of faces that are upright in img."""
    s = FACE_SIZE / max(img.shape[:2])
    small = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s < 1 else img
    det.setInputSize((small.shape[1], small.shape[0]))
    _, faces = det.detect(small)
    total = 0.0
    for f in faces if faces is not None else ():
        if f[14] < FACE_MIN_SCORE:
            continue
        # Landmarks: right eye, left eye, nose, right and left mouth corner.
        # The detector also fires on faces turned well off upright, so check
        # the landmarks really are in upright order.
        reye, leye, nose = f[4:6], f[6:8], f[8:10]
        eyes, mouth = (reye + leye) / 2, (f[10:12] + f[12:14]) / 2
        down = mouth - eyes
        tilt = abs(np.degrees(np.arctan2(down[0], down[1])))
        if tilt < 35 and leye[0] > reye[0] and eyes[1] < nose[1] < mouth[1]:
            total += float(f[14])
    return total


def upright_turn(img):
    """Clockwise quarter turn (0, 90, 180 or 270) that makes the photo upright."""
    global _face_detector
    if _face_detector is None:
        if not FACE_MODEL.exists():
            print(f"(auto-rotate off: {FACE_MODEL} is missing)", flush=True)
            _face_detector = False
        else:
            try:   # OpenCV prints a harmless backend warning otherwise
                cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
            except AttributeError:
                pass
            _face_detector = cv2.FaceDetectorYN.create(str(FACE_MODEL), "", (320, 320),
                                                       0.5, 0.3, 5000)
    if not _face_detector:
        return 0
    # A photo that already has an upright face stays as it is. This keeps
    # mixed pages (e.g. a sheet of school portraits) from being turned.
    if _faces_upright_score(_face_detector, img) >= FACE_MIN_SCORE:
        return 0
    scores = {a: _faces_upright_score(_face_detector, cv2.rotate(img, r))
              for a, r in _TURNS.items()}
    best = max(scores, key=scores.get)
    runner_up = max(v for a, v in scores.items() if a != best)
    # Weak detections show up in wrong orientations too, so only turn when
    # the winning face is clearly convincing. Leaving a photo as scanned is
    # better than turning it the wrong way.
    if scores[best] >= TURN_SURE_SCORE or (scores[best] >= TURN_MIN_SCORE
                                           and runner_up < FACE_MIN_SCORE):
        return best
    return 0


def turn(img, angle):
    return img if angle == 0 else cv2.rotate(img, _TURNS[angle])


# ---- brightening dark photos ------------------------------------------------
# The scanner can't be adjusted, and old prints often come out dark. A photo
# whose typical (median) brightness is below DARK_MEDIAN has its black and
# white points set and its mid-tones lifted towards TARGET_MEDIAN, on the
# lightness channel only so colours stay as they are. The lift is capped so a
# real night photo isn't turned into daylight. Other photos are untouched.
DARK_MEDIAN = 0.40        # median lightness (0-1) below which a photo is "dark"
TARGET_MEDIAN = 0.45      # lift dark photos' median towards this
MIN_GAMMA = 0.55          # strongest lift allowed (gamma; 1 = none)


def brighten(img):
    """Return (image, True) with a dark photo brightened, or (img, False)."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    light = lab[..., 0].astype(np.float32) / 255
    if float(np.median(light)) >= DARK_MEDIAN:
        return img, False
    # Stretch only a squashed range: never move the black point above 6% or
    # the white point below 85%, so little detail is clipped.
    lo = min(float(np.percentile(light, 0.5)), 0.06)
    hi = max(float(np.percentile(light, 99.5)), 0.85)
    light = np.clip((light - lo) / (hi - lo), 0, 1)
    med = max(float(np.median(light)), 1e-3)
    if med < TARGET_MEDIAN:
        light **= max(np.log(TARGET_MEDIAN) / np.log(med), MIN_GAMMA)
    lab[..., 0] = np.clip(light * 255 + 0.5, 0, 255).astype(np.uint8)
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR), True


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


def draw_boxes(img, rects, missed=(), cutoff=()):
    """Copy of the page with extracted photos boxed in red (numbered), cut-off
    photos in magenta (CUT OFF n) and missed regions in yellow (MISSED n)."""
    dbg = img.copy()
    font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, 3.0, 8

    def box(rect, colour, label):
        pts = cv2.boxPoints(rect).astype(np.int32)
        cv2.drawContours(dbg, [pts], 0, colour, 10)
        # Keep the label on the page even when the box starts above or left of it.
        x = max(pts[:, 0].min(), 0) + 20
        y = min(max(pts[:, 1].min(), 0) + 110, dbg.shape[0] - 30)
        cv2.putText(dbg, label, (int(x), int(y)), font, scale, (0, 0, 0), thick + 8)
        cv2.putText(dbg, label, (int(x), int(y)), font, scale, colour, thick)

    for i, r in enumerate(rects):
        if i in cutoff:
            box(r, (255, 0, 255), f"CUT OFF {i + 1}")
        else:
            box(r, (0, 0, 255), str(i + 1))
    for i, m in enumerate(missed, 1):
        box(m["rect"], (0, 200, 255), f"MISSED {i}")
    return dbg


def _where(rect, shape):
    """'top', 'middle' or 'bottom' (plus left/right if off-centre) of the page."""
    (cx, cy), _, _ = rect
    h, w = shape[:2]
    v = "top" if cy < h / 3 else "bottom" if cy > 2 * h / 3 else "middle"
    hz = " left" if cx < w / 3 else " right" if cx > 2 * w / 3 else ""
    return v + hz


def _upright_size(rect):
    """(width, height) of a rotated rect as it will look once straightened."""
    _, (w, h), ang = rect
    while ang > 45:
        ang -= 90
        w, h = h, w
    while ang < -45:
        ang += 90
        w, h = h, w
    return w, h


def _size_text(rect):
    w, h = _upright_size(rect)
    return f"{int(w)}x{int(h)} px, about {w / page_dpi:.1f} x {h / page_dpi:.1f} in"


def write_report(out: Path, stamp: str, source: str, kept_at: str, img=None,
                 rects=(), names=(), missed=(), cutoff=(), recovered=(), error=None):
    """Write <stamp>_PROBLEM.txt (and a marked-up page image) explaining a
    scan whose photos weren't all extracted cleanly. Returns the report path."""
    report = unused_name(out, f"{stamp}_PROBLEM", ".txt")
    boxes = None
    if img is not None:
        boxes = report.with_name(report.stem + "_boxes.jpg")
        cv2.imwrite(str(boxes), draw_boxes(img, rects, missed, cutoff),
                    [cv2.IMWRITE_JPEG_QUALITY, 80])

    lines = [
        "photoScanner: some photos on this scan were not extracted cleanly",
        "=" * 64,
        "",
        f"Scan file:     {source}",
        f"Scan kept at:  {kept_at}",
        f"Scanned:       {stamp[:10]} {stamp[11:13]}:{stamp[13:15]}",
        f"Checked:       {datetime.now():%Y-%m-%d %H:%M}",
    ]
    if boxes:
        lines.append(f"Marked-up page: {boxes.name}")
        lines.append("  (red = extracted, magenta = cut off, yellow = missed)")
    lines.append("")

    if error:
        lines += ["PROBLEM", f"  The scan couldn't be read: {error}",
                  "  It was probably still being written, or the file is damaged.",
                  "  It stays in the scans folder and is retried if it changes;",
                  "  otherwise scan the page again.", ""]
    else:
        lines.append(f"EXTRACTED ({len(names) - len(cutoff)})")
        for i, (n, r) in enumerate(zip(names, rects)):
            if i in cutoff:
                continue
            note = "  (recovered: outline merged with sleeve or pale edge; check the crop)" \
                if i in recovered else ""
            lines.append(f"  {n}  {_where(r, img.shape)}, {_size_text(r)}{note}")
        if len(names) == len(cutoff):
            lines.append("  none")
        lines.append("")

        if cutoff:
            lines.append(f"CUT OFF BY THE SCANNER EDGE ({len(cutoff)}), saved but NOT uploaded")
            for i in cutoff:
                lines.append(f"  {names[i]}  {_where(rects[i], img.shape)}, {_size_text(rects[i])}")
            lines += ["  These touch the edge of the scan and are much smaller than the",
                      "  other photos on the page, so part of each is probably missing.", ""]

        lines.append(f"MISSED ({len(missed)})")
        if not missed and not rects:
            lines += ["  No photo-sized region was found at all. The page may be",
                      "  blank, or every photo is black-and-white / very faded",
                      "  (detection relies on colour)."]
        elif not missed:
            lines.append("  none")
        for i, m in enumerate(missed, 1):
            r = m["rect"]
            lines.append(f"  {i}. {_where(r, img.shape)} of the page, {_size_text(r)} "
                         f"({m['area_frac'] * 100:.1f}% of the page)")
            if m.get("kind") == "off-scanner":
                lines.append("     Only a small piece is on the scan, at its edge: the photo")
                lines.append("     was probably lying mostly off the scanner glass.")
            else:
                lines.append(f"     Its outline only fills {m['fill'] * 100:.0f}% of its bounding "
                             f"box (a photo should fill {MIN_FILL * 100:.0f}%), and it")
                lines.append("     couldn't be straightened out. Often several small photos")
                lines.append("     overlapping in one slot, or a photo merged with the page edge.")
        lines.append("")

    lines += [
        "WHAT TO DO",
        "  - Rescan the page with the album page fully on the glass, slightly",
        "    away from the scanner's edges.",
        "  - Or crop the photo by hand from the scan above.",
        "  - To see the detection again:",
        f"      python3 split_photos.py \"{kept_at}\" --keep --debug -o /tmp/check",
        "",
        "This file, the marked-up image and *_CUTOFF.jpg files are not uploaded",
        "to Google Photos; delete them once you've dealt with the scan.",
    ]
    report.write_text("\n".join(lines) + "\n")
    return report


def process(f: Path, args, tmp: Path):
    """Split one scan into photos; move it to done/ unless --keep."""
    out = Path(args.out).expanduser() if args.out else f.parent / "photos"
    out.mkdir(parents=True, exist_ok=True)
    img = load_page(f, tmp)
    missed, recovered = [], []
    rects = find_photos(img, missed, recovered)
    cutoff = cut_off(rects, img.shape)
    stamp = scan_stamp(f)
    names, turned, brightened = [], [], []
    for i, r in enumerate(rects):
        # Cut-off photos are kept for reference but named so they aren't uploaded.
        dest = unused_name(out, f"{stamp}_{i + 1}" + ("_CUTOFF" if i in cutoff else ""), ".jpg")
        photo = crop_rect(img, r)
        angle = 0 if args.no_rotate else upright_turn(photo)
        if angle:
            turned.append(f"{dest.name} {angle}°")
        photo = turn(photo, angle)
        if not args.no_brighten:
            photo, lifted = brighten(photo)
            if lifted:
                brightened.append(dest.name)
        cv2.imwrite(str(dest), photo, [cv2.IMWRITE_JPEG_QUALITY, 95])
        names.append(dest.name)
    if args.debug:
        cv2.imwrite(str(unused_name(out, f"{stamp}_boxes", ".jpg")),
                    draw_boxes(img, rects, missed, cutoff))
    moved, kept_at = "", f
    if args.keep:
        ledger_add(f)
    else:
        done = f.parent / "done"
        done.mkdir(exist_ok=True)
        target = unused_name(done, f.stem, f.suffix)
        shutil.move(str(f), str(target))
        moved, kept_at = f", moved to done/{target.name}", target
    extra = f" ({len(recovered)} recovered)" if recovered else ""
    print(f"{f.name}: {len(rects) - len(cutoff)} photo(s){extra} -> "
          f"{', '.join(n for i, n in enumerate(names) if i not in cutoff) or 'none'}{moved}",
          flush=True)
    if turned:
        print(f"  turned upright: {', '.join(turned)}", flush=True)
    if brightened:
        print(f"  brightened: {', '.join(brightened)}", flush=True)
    if missed or cutoff or not rects:
        report = write_report(out, stamp, f.name, str(kept_at), img, rects, names,
                              missed, cutoff, recovered)
        problems = [f"{len(cutoff)} cut off" if cutoff else "",
                    f"{len(missed)} missed" if missed else "",
                    "none found" if not rects and not missed else ""]
        print(f"  {', '.join(p for p in problems if p)}; see {report.name}", flush=True)


def report_unreadable(f: Path, args, error):
    """Write a problem report for a scan that couldn't be read at all."""
    out = Path(args.out).expanduser() if args.out else f.parent / "photos"
    out.mkdir(parents=True, exist_ok=True)
    try:
        stamp = scan_stamp(f)
    except OSError:
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    return write_report(out, stamp, f.name, str(f), error=str(error))


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
    reports = {}     # path -> problem report written for an unreadable scan
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
                stale = reports.pop(f, None)   # it worked after all
                if stale:
                    stale.unlink(missing_ok=True)
            except Exception as e:  # keep watching even if one scan is bad
                failed[f] = sig
                print(f"{f.name}: could not split ({e}); will retry if it changes",
                      flush=True)
                if f not in reports:
                    try:
                        reports[f] = report_unreadable(f, args, e)
                    except Exception:
                        pass
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
    if args.no_rotate:
        prog.append("--no-rotate")
    if args.no_brighten:
        prog.append("--no-brighten")
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
    ap.add_argument("--no-rotate", action="store_true",
                    help="don't turn sideways photos upright")
    ap.add_argument("--no-brighten", action="store_true",
                    help="don't brighten dark photos")
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
                try:
                    print(f"  see {report_unreadable(f, args, e).name}")
                except Exception:
                    pass
        if args.upload:
            try:
                upload(args)
            except Exception as e:
                sys.exit(f"Google Photos upload failed: {e}")


if __name__ == "__main__":
    main()
