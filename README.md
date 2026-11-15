# photoScanner

Split scanned photo-album pages into one image per photo, entirely on your own machine.

Scan an album page (with several photos in plastic sleeves) on a flatbed scanner, run the script, and each photo comes out as its own straightened JPEG.

## How it works

The album sleeve is almost colourless, while photos (even pale ones like snow scenes) have colour. The script:

1. Renders each scanned PDF (or reads a JPEG/PNG/TIFF).
2. Marks every pixel that is noticeably coloured or dark.
3. Cleans that mask up and finds each large, solid rectangle — one per photo.
4. Straightens slightly tilted photos and saves each one.

Photos are found on every page, not cut at fixed positions, so photos sitting a little differently from page to page are handled automatically.

## Setup

Requires Python 3, [ImageMagick](https://imagemagick.org) and Ghostscript (for PDFs):

```bash
brew install imagemagick ghostscript
python3 -m pip install --user -r requirements.txt
```

## Usage

```bash
python3 split_photos.py ~/scans
```

- Photos are saved to `~/scans/photos/`, named by when the page was scanned: `2026-11-15_1527_1.jpg`, `_2`, `_3` … top to bottom.
- Each scan that has been split is moved to `~/scans/done/`, so the next run only handles new scans.
- Existing files are never overwritten; a clashing name gets `-2`, `-3` … added.

Options:

| Option | What it does |
|---|---|
| `-o DIR` | Save photos to `DIR` instead of `<scans>/photos` |
| `--keep` | Leave scans in place instead of moving them to `done/` (a hidden `.split_photos_done` file remembers which were split) |
| `--debug` | Also save a copy of each page with red boxes around the photos found |
| `--watch` | Keep running and split each new scan as it arrives |
| `--interval N` | Seconds between checks in watch mode (default 5) |
| `--install` | macOS: run watch mode in the background, now and at every login |
| `--uninstall` | macOS: stop and remove the background watcher |
| `--upload` | Also upload photos to Google Photos (see below) |
| `--album NAME` | Google Photos album to use (default "Photo Album Scans") |
| `--photos-login` | Sign in to Google Photos once |

## Watching the folder automatically

```bash
python3 split_photos.py ~/scans --watch
```

checks the folder every few seconds. A scan is split only once it has finished arriving (its size stopped changing), which matters when a scanner writes it over the network. A damaged file is reported once and retried only if it changes.

To have that run all the time without a terminal open (macOS):

```bash
git clone https://github.com/ndipatri/photoScanner.git ~/photoScanner
cd ~/photoScanner
python3 split_photos.py ~/scans --install
```

This adds a LaunchAgent (`~/Library/LaunchAgents/com.photoscanner.watch.plist`) that starts the watcher immediately, restarts it if it stops, and starts it again at every login. Any other options you pass (`-o`, `--keep`, `--debug`, `--interval`) are kept. Output goes to `~/Library/Logs/photoScanner.log`:

```bash
tail -f ~/Library/Logs/photoScanner.log
```

Clone the repo somewhere permanent first — the agent runs the script from wherever it was installed. To stop it for good:

```bash
python3 split_photos.py --uninstall
```

## Uploading to Google Photos

With `--upload`, every photo is also added to a Google Photos album (default "Photo Album Scans"; change it with `--album NAME`). The script only asks for Google's *append-only* permission: it can add photos and create its own album, but can't see, change or delete anything else in your library. A hidden `.uploaded_to_google_photos` file in the photos folder remembers what's been uploaded, so nothing goes up twice and failed uploads are retried.

### One-time Google Cloud setup

Google requires your own (free) API credentials:

1. Go to [console.cloud.google.com](https://console.cloud.google.com) and create a project, e.g. `photoScanner`.
2. **APIs & Services → Library**: enable **Photos Library API**.
3. **Google Auth Platform → Branding** (OAuth consent screen): app name `photoScanner`, your email as support and developer contact; audience **External**.
4. **Audience**: click **Publish app** so it's "In production". (In "Testing", Google expires the sign-in every 7 days. You'll see an "unverified app" warning when signing in — that's expected for a personal app.)
5. **Data Access**: add the scope `https://www.googleapis.com/auth/photoslibrary.appendonly`.
6. **Clients → Create client**: type **Desktop app**. Download the JSON and save it as `~/.config/photoScanner/client_secret.json`.

Then sign in once (opens your browser):

```bash
python3 split_photos.py --photos-login
```

and include `--upload` when running or installing:

```bash
python3 split_photos.py ~/scans --upload --install
```

Photos already in the folder are uploaded on the first run.

## Tips for scanning

- Use **A4** scan size on a Letter-sized flatbed — the extra length usually captures the bottom photo of an album page.
- Photos stored sideways in the album come out sideways; rotate them afterwards.

## Limitations

- Black-and-white or very faded photos may be missed, since detection is colour-based. Use `--debug` to check.
- Photos touching each other with no sleeve gap between them may come out as one image.
