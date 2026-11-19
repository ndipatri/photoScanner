---
name: photoscanner-setup
description: Set up, reinstall, extend or troubleshoot photoScanner, the pipeline that splits scanned photo-album pages into one image per photo on a Mac, runs as a background watcher (LaunchAgent), and uploads the photos to a Google Photos album through the user's own Google Cloud OAuth app. Use this whenever the user wants to install photoScanner on a new Mac, change the scan folder or album, re-run or debug the Google Photos sign-in ("Access blocked", "unverified app", token expired), fix scan-to-network-folder from an HP printer, see why the watcher isn't splitting or uploading, or touch split_photos.py / google_photos.py, even if they don't say "photoScanner" by name.
---

# photoScanner setup

photoScanner turns album-page scans into individual photos in Google Photos with no manual steps:

```
HP printer --(Scan to Network Folder, SMB)--> ~/scans/*.pdf
   --> split_photos.py --watch (LaunchAgent)  --> ~/scans/photos/<scan-time>_N.jpg, scan moved to ~/scans/done/
   --> google_photos.py (append-only API)    --> Google Photos album "Photo Album Scans"
```

Repo: https://github.com/ndipatri/photoScanner (`split_photos.py`, `google_photos.py`, `README.md`, `PRIVACY.md`).

The setup has four independent layers. Find out which one the user needs before doing anything; most requests touch only one:

| Layer | What it is | Details |
|---|---|---|
| 1. Printer → Mac | Printer saves scans into a shared folder on the Mac | `references/printer-and-mac.md` |
| 2. Splitter | `split_photos.py` cuts each page into photos | this file |
| 3. Watcher | macOS LaunchAgent runs the splitter continuously | this file |
| 4. Google Photos | Google Cloud OAuth app + one-time sign-in | `references/google-cloud.md` |

Check the current state first. It is cheap and avoids redoing finished work:

```bash
ls ~/scans ~/scans/photos ~/scans/done | head            # folders and recent output
launchctl print gui/$(id -u)/com.photoscanner.watch | grep -E "^\s+state ="   # watcher running?
tail -20 ~/Library/Logs/photoScanner.log                 # what it did / errors
ls -l ~/.config/photoScanner/                            # client_secret.json, google_token.json, google_albums.json
```

## Things the user must do themselves

Several steps involve credentials, security settings or legal agreements. Claude can't do these steps, so plan around them and say up front which ones are coming:

- **Typing passwords**: the printer admin password, the Google sign-in, the Mac password for SMB. Have the user type them. For a password-only HTTP Basic login (the HP web page), they can put it in the URL themselves: `http://:PASSWORD@<printer-ip>/<protected page>`.
- **macOS security and network settings**: the firewall, File Sharing, DNS servers. Give them the exact menu path.
- **Agreeing to Google's terms or the API Services User Data Policy, and downloading the OAuth client JSON.** Ask the user before each one. Once they say yes, you can click it.
- **Approving the Google consent screen in their browser.**

## 2. Splitter: `split_photos.py`

How it works: `load_page` takes the scanner's own image at full resolution. An HP scan PDF is one DCT (JPEG) image, which `_pdf_jpeg` pulls out and decodes directly (no Ghostscript, about 50× faster). The dpi comes from the MediaBox and is kept in `page_dpi` for report sizes and the crop inset. Any other PDF is rendered by ImageMagick/Ghostscript at 600 dpi. A scan below 300 dpi is enlarged to 300 dpi by pixel repetition (`INTER_NEAREST`), which matches what Ghostscript did when detection was tuned. Detection on these pages is borderline: a 1 px change in page width flipped two recoveries. On 600 dpi scans the new loader gives exactly the same results as the old 300 dpi render. To check the resolution of a scan, run `python3 -c "import split_photos as s, pathlib; print(s._pdf_jpeg(pathlib.Path('SCAN.pdf'))[1])"`. The first 27 pages of the original album came in at 150 dpi, and pages from scan-28 on at 600 dpi, JPEG quality about 75 and 4:2:0 chroma. Detection then works on a 1200 px wide copy. A pixel counts as "photo" when its HSV saturation is above 25 or its value is below 150. That separates photos from the album's colourless plastic sleeve, including pale photos like snow scenes, so it works no matter where the photos sit on the page. The mask is opened then closed with a 15 px kernel. Each blob larger than 2% of the page that fills at least 80% of its `minAreaRect` becomes one photo. Slightly tilted photos are straightened, with the angle normalized to ±45°. Output is sorted top to bottom.

Install the dependencies:

```bash
brew install imagemagick ghostscript
python3 -m pip install --user -r requirements.txt   # numpy, opencv-python-headless, google-auth-oauthlib, requests
```

Behaviour that matters when changing it:
- Photos are named by the **scan's modification time** (`2026-11-15_1527_1.jpg`), not the PDF name, because the printer reuses names like `scan.pdf`. Existing files are never overwritten; a clash gets `-2`, `-3`.
- Each scan that has been split is **moved to `done/`**, so the next run only sees new files. With `--keep`, a hidden `.split_photos_done` ledger records scans instead.
- **Problem reports.** `find_photos(img, missed)` also returns photo-sized blobs that it skipped: at least 2% of the page, but filling less than 80% of their `minAreaRect`. When there are any, or when no photos are found, `process()` writes `photos/<stamp>_PROBLEM.txt` and `photos/<stamp>_PROBLEM_boxes.jpg` (extracted photos in red, missed ones in yellow). An unreadable scan also gets a report. In watch mode, that report is deleted if the scan later splits successfully, for example once a half-arrived file finishes arriving. The uploader skips anything with `_boxes` in its name, and `.txt` files are never uploaded. Check these reports first when the user says a photo is missing.
- **Recovery of misshapen blobs** (`_recover`). In real scans, missed photos had two causes: the photo merged with the sleeve's yellowed edge, or pale sky or snow (saturation about 5, value about 210, the same as the sleeve) notched its outline. A blob that fails the 80% fill test now goes through four steps, in order:
  1. Stricter colour thresholds (saturation above 40–90), which drop the yellowed plastic.
  2. `_snap_to_edges`: in the rotated frame, each side moves outward to the first row or column where at least 70% of the side has a gradient above 10, meaning the photo's border.
  3. The convex hull, if it is itself a clean rectangle.
  4. A much larger morphological opening, which cuts off thin attachments.

  A recovered rect that covers more than 35% of the page, or overlaps a normally found photo by more than 5%, is rejected and reported. This came out of tuning on 23 real pages: 12 of 13 missed photos were recovered with no false positives. The remaining failure is overlapping, tilted wallet photos. `find_photos(img, missed, recovered)` reports the indexes of recovered photos, and their report lines say "check the crop".
- **Flip-album pocket stacks** (`_Stack`). In a flip album the prints sit in overlapping pink pockets; colour merges the visible photos, the pocket strips behind them and the album cover into one blob (scan-62: 57% of the page, scan-63: 85%), which used to be saved and uploaded as one whole-page "photo". Any blob over `STACK_MIN_FRAC` (40%; the biggest normal photo so far was a 5×7 at 33%) is straightened by its angle and tested with `is_stack`. If its fill is at least 0.9 and every side has straight-edge support of at least `SINGLE_PRINT_SIDES` (0.6), it's a single big print and is kept whole. A stack's outline scores 0 on at least one side. Otherwise it's a stack if a pocket boundary crosses it or it has `STACK_SHINGLES` (10) long parallel edges. Line counts and mat fraction alone did NOT separate stacks from normal photos; striped or wallpapered photos score higher. So the size gate and the outline test are what protect normal pages. `photos()` builds candidate boxes from long straight rows and columns (runs ≥ 1.25 in, neighbours merged), with sides 2.5–8.5 in, and each side ≥ 70% on an edge (±3 px tolerance). It drops boxes that are over 25% "mat" (flat pink mat, flat pale sleeve or glare, flat dark cover) or that have a pocket boundary inside. A pocket boundary is an inner line ≥ 95% complete, or ≥ 85% with pink or flat mat beside it; door frames in the photo were about 88% with no mat beside them. The remaining boxes are scored +1 per photo pixel and −3 per mat pixel, and the best non-overlapping ones are kept. Growing a box over pocket strips or sleeve lowers its score. `leftovers()` reports photo content of at least 1.5 in on both sides that reaches the scan edge outside the chosen boxes as missed, kind `partly-off`, which is a photo hanging off the glass. A stack with no box found is reported as kind `stack` and nothing is uploaded. Tuned on scans 62–63 only: it found all 3 fully visible photos and reported the cut-off one. The other 62 past scans were checked to give identical results. `find_photos(..., pocketed)` returns their indexes, the log says `(n from a pocket stack)`, and report lines say "check the crop".
- **Cut-off photos** (`cut_off`). A photo that touches the scan edge (within 1%) and is smaller than 0.6 × the median area of the page's other photos is saved as `<stamp>_N_CUTOFF.jpg`, isn't uploaded (the uploader skips `_CUTOFF`), and is listed in the report.
- **Slivers.** A blob of 0.5–2% of the page at the scan edge, with fill ≥ 0.75, a short side ≥ 1.5% of the page width, and median saturation ≥ 60 or value ≤ 120, is reported as "off-scanner", meaning the photo was mostly off the glass. The colour test keeps out yellowed sleeve corners and page-edge strips, which caused false alarms without it.
- **Auto-rotation** (`upright_turn`). Each crop is checked with OpenCV's YuNet face detector (`models/face_detection_yunet_2023mar.onnx`, 230 KB, from opencv_zoo, committed to the repo; needs `cv2.FaceDetectorYN`, OpenCV 4.8+). A face counts only if its confidence is at least 0.75 and its landmarks are in upright order (eyes above nose above mouth, eye line within 35° of level); YuNet also fires on faces turned 90° or 180°, so the landmark check matters. A photo with an upright face as scanned is never turned, which protects mixed portrait sheets. Otherwise the best quarter turn wins if its face scores at least 0.88, or at least 0.80 with no face in any other turn. On the 87 photos of the first 34 pages, 10 sideways photos were turned correctly and none wrongly; an earlier looser rule (0.75) turned an upright wedding photo, because weak detections appear in wrong orientations. Turned photos are listed in the log as `turned upright: <name> 90°`. `--no-rotate` turns it off (and `--install` passes it on). If the model file is missing, rotation is skipped with a one-line notice.
- **Brightening** (`brighten`). The user's scanner can't be adjusted and scans came out dark. After rotation, a photo whose median LAB lightness is below `DARK_MEDIAN` (0.40) is adjusted on L only: the black point is set at no more than 6% and the white point at no less than 85% (0.5/99.5 percentiles), then a gamma lifts the median towards `TARGET_MEDIAN` (0.45), capped at `MIN_GAMMA` 0.55. Other photos are untouched. On the first 38 pages this brightened 59 of 94 photos. Night and flash shots and dim interiors improved clearly; a blurry close-up with big highlights came out a bit washed out. It does not fix colour casts from faded prints. The log says `brightened: <name>`. `--no-brighten` turns it off (`--install` passes it on).
- `--debug` writes `<stamp>_boxes.jpg` with the same red and yellow boxes, even for pages that split fine.
- Known limits: black-and-white or very faded photos may be missed (detection is colour-based), photos touching each other merge into one, sideways photos without a clear face stay sideways, and a person lying down can be turned so their face is upright while the scene is on its side.

To test detection changes, run on a copy and look at the crops. Don't test on the live `~/scans`, because the scans get moved:
```bash
python3 split_photos.py /path/to/copy --keep --debug -o /tmp/out
```

## 3. Watcher (macOS LaunchAgent)

`--watch` checks the folder every 5 s. It only splits a file whose size and modification time are the same on two checks in a row, because the printer writes over the network and a half-written PDF fails. A broken file is reported once and retried only if it changes.

Install it from a **permanent clone**. The agent runs the script from the path it was installed from, so a temp or session folder breaks it later:

```bash
git clone https://github.com/ndipatri/photoScanner.git ~/photoScanner
cd ~/photoScanner && python3 split_photos.py ~/scans --upload --install
```

`--install` writes `~/Library/LaunchAgents/com.photoscanner.watch.plist` with `RunAtLoad`, `KeepAlive` and logging to `~/Library/Logs/photoScanner.log`, then bootstraps it. It also passes along `-o`, `--keep`, `--debug`, `--interval`, `--upload` and `--album`. When `--upload` is given, it first checks the Google sign-in so a broken sign-in fails right away rather than silently in the background.

Gotchas we hit:
- launchd starts with a bare PATH. The plist sets `PATH` to include `/opt/homebrew/bin`; without it, `magick` isn't found.
- Reinstalling right after `bootout` can fail with `Bootstrap failed: 5: Input/output error`, because the old job is still exiting. The installer now waits for the old job to disappear and retries. If it still fails, wait a few seconds and run `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.photoscanner.watch.plist`.
- When you test `--watch` from a shell, don't start it with `&` and stop it with `kill -INT`. Background jobs in non-interactive shells ignore SIGINT, so stray watchers keep running and grab test files. Use `kill` (SIGTERM) and check with `pgrep -fl split_photos`.
- To update the watcher after code changes, run `git -C ~/photoScanner pull`. If the flags change, re-run `--install`; otherwise, restart it with `launchctl kickstart -k gui/$(id -u)/com.photoscanner.watch`.

Remove it with `python3 split_photos.py --uninstall`.

## 4. Google Photos upload

`--upload` adds new photos to an album that the script creates once (default "Photo Album Scans", change with `--album`). It asks only for the scope `https://www.googleapis.com/auth/photoslibrary.appendonly`: it can add photos and create its own album, but can't read, change or delete anything else in the library. The album ID is cached in `~/.config/photoScanner/google_albums.json`. Uploaded file names go into `photos/.uploaded_to_google_photos`, so nothing is uploaded twice and failed uploads are retried. Photos Google rejects are skipped until the watcher restarts.

**This network has intermittent DNS failures.** Comcast's resolvers sometimes refuse lookups such as `oauth2.googleapis.com`. Every Google call, including the sign-in token exchange, is wrapped in `with_retry`, which retries network and DNS errors for up to 5 minutes. A real authentication error such as `invalid_grant` still fails straight away. Keep this in any new network code, and prefer retrying over asking the user to change DNS. In watch mode, the `Uploader` also backs off for 30 s, 1 min, 5 min, then 15 min after a failed upload, and prints each distinct error only once.

The one-time Google Cloud setup (project, API, consent screen, publishing, Desktop client) is in `references/google-cloud.md`. Read it before touching the Cloud console; several steps have non-obvious traps.

Signing in:
```bash
cd ~/photoScanner && python3 split_photos.py --photos-login
```
This always runs a fresh browser sign-in, even if an older sign-in is still valid, which matters after the app is published. The user sees "Google hasn't verified this app": they click **Advanced → Go to photoScanner (unsafe)**, then allow access. The token is saved to `~/.config/photoScanner/google_token.json` (mode 600). A running watcher picks up the new token on its next upload, with no restart needed.

## Troubleshooting quick map

| Symptom | Cause / fix |
|---|---|
| Printer "Save and Test" fails | macOS firewall blocking `smbd` (System Settings → Network → Firewall → Options → allow File Sharing). Then check SMB is on for the user. See `references/printer-and-mac.md`. |
| Bottom photo cut off | Album page is longer than Letter size. Set the scan size to **A4** (the M477 glass maxes out at A4; Legal only works in the document feeder). |
| Page yields fewer photos than expected | Read `photos/<stamp>_PROBLEM.txt` and its `_PROBLEM_boxes.jpg` (red = extracted, magenta = cut off, yellow = missed). Remaining causes: overlapping wallet photos in one slot, faded or B&W photos, photos touching, or a photo off the glass. |
| Log: `not signed in` / `sign-in expired` | Run `--photos-login`. If it expires weekly, the app is still in **Testing**; publish it (`references/google-cloud.md`). |
| Browser: "Access blocked … has not completed the Google verification process" (403 access_denied) | App is in Testing and the account isn't a test user, or the app isn't published. |
| `Failed to resolve 'oauth2.googleapis.com'` | Flaky ISP DNS; the retry wrapper handles it. If it lasts more than 5 minutes, try again later. |
| Watcher not running after reboot | `launchctl print gui/$(id -u)/com.photoscanner.watch`; re-run `--install` from `~/photoScanner`. |
