# photoScanner

**Turn a physical photo album into a Google Photos album, using just a flatbed scanner and a Mac.**

Lay a whole album page on the scanner, with the photos still in their sleeves, and press Scan. A few seconds later, every photo on that page shows up as its own straightened image in a Google Photos album, turned the right way up even if it was stored sideways in the album. You don't take any photos out of the album, and you don't crop anything, rotate anything, rename anything or upload anything by hand.

```
 album page ──► scanner ──► folder on your Mac ──► split into photos ──► Google Photos album
              (scan to folder)   (~/scans)       (one JPEG per photo)   ("Photo Album Scans")
```

A small background service on the Mac does all of this:

1. **Waits for scans.** It watches a folder that your scanner saves into, for example with a network printer's "Scan to Network Folder".
2. **Splits each page.** It finds each photo on the album page, even when photos sit at slightly different spots on each page or are a little crooked. It straightens them, turns photos that were stored sideways or upside down the right way up, brightens dark ones, and saves each one as a JPEG.
3. **Uploads to Google Photos.** It adds the photos to an album in your Google Photos library. The script can only *add* photos. It can't see, change or delete anything else in your library.

Everything runs on your Mac with ordinary image-processing code, with no AI service involved. The only place photos are sent is your own Google account.

## What you need

- A Mac (macOS) with [Homebrew](https://brew.sh) and Python 3
- A flatbed scanner that can save PDFs (or JPEG/PNG/TIFF) into a folder on the Mac. A network printer's "Scan to Network Folder" works best: you never have to touch the Mac.
- A Google account, and a free Google Cloud project for the Photos upload. Setting that up takes about 10 minutes, once.

## Set it up with an AI agent (recommended)

This repo includes an **agent skill**, [`.agents/skills/photoscanner-setup/`](.agents/skills/photoscanner-setup/SKILL.md). It gives an AI coding agent everything it needs to set up the whole system, troubleshoot it, or extend it:

- installing the tools
- sharing the scan folder and setting up the printer
- the Google Cloud project, consent screen and sign-in
- the background service
- the non-obvious problems hit on a real setup: a firewall blocking file sharing, Google's 7-day sign-in expiry, an unreliable home DNS, and more

Open this repo in an agent that supports skills (for example [Claude Code](https://claude.com/claude-code)) and ask:

> Set up photoScanner on this Mac so my album scans end up in Google Photos.

The agent works through the steps below and pauses for the things only you should do: typing passwords, changing macOS security settings, agreeing to Google's terms, and approving the sign-in in your browser.

- **Claude Code** finds the skill automatically through the `.claude/skills` link.
- **Other agents:** point them at `.agents/skills/photoscanner-setup/SKILL.md`.

## Setup

### 1. Install

```bash
brew install imagemagick ghostscript
git clone https://github.com/ndipatri/photoScanner.git ~/photoScanner
cd ~/photoScanner
python3 -m pip install --user -r requirements.txt
```

Keep the clone somewhere permanent. The background service runs the script from wherever you install it.

### 2. Get scans into a folder

Make a folder for scans, for example `~/scans`, and point your scanner at it.

**For a network printer (SMB "Scan to Network Folder"):**

1. On the Mac, go to **System Settings → General → Sharing → File Sharing (ⓘ)**. Add `~/scans` with Read & Write access for your user. Under **Options…**, turn on **Share files and folders using SMB** and tick your account.
2. If the macOS firewall is on, go to **System Settings → Network → Firewall → Options** and allow incoming connections for **File Sharing (smbd)**. A blocked `smbd` is the most common reason the printer's folder test fails.
3. In the printer's web page, add a network folder:
   - **Path:** `\\<your-mac's-IP>\scans`
   - **Username and password:** your Mac's short username (`whoami`) and its login password
   - Then use **Save and Test**.

Give the Mac a fixed IP address with a DHCP reservation in your router, so the path keeps working. Also turn on **System Settings → Energy → Wake for network access**, so the Mac can receive scans while it's asleep.

### 3. Connect Google Photos (one time)

Google requires every app that uploads to Google Photos to have its own API credentials. You create these in your own free Google Cloud project, at [console.cloud.google.com](https://console.cloud.google.com):

1. **Create a project**, for example `photoScanner`. If the form requires a billing account, linking one is fine; the Photos Library API doesn't cost anything.
2. **APIs & Services → Library:** enable the **Photos Library API**.
3. **Google Auth Platform → Branding:** create the consent screen.
   - App name `photoScanner`, with your email as the support and developer contact.
   - Audience: **External**.
   - Fill in an **Application home page**, a **privacy policy link** and a matching **authorized domain**. For example, use this repo's URL, [PRIVACY.md](PRIVACY.md), and `github.com`. Google won't let you publish without them.
4. **Data Access:** add the scope `https://www.googleapis.com/auth/photoslibrary.appendonly`.
5. **Clients → Create client → Desktop app.** Click **Download JSON** in the dialog that appears; Google shows the secret only once. Save the file as `~/.config/photoScanner/client_secret.json`.
6. **Audience → Publish app.** Leave the app in "Testing" and Google signs you out every 7 days. You don't need Google's verification for personal use; the only effect is a warning when you sign in.

Then sign in. Your browser opens:

```bash
python3 split_photos.py --photos-login
```

On "Google hasn't verified this app", click **Advanced → Go to photoScanner (unsafe)**. It's your own app. Then allow **Add to your Google Photos library**. If you signed in before publishing, sign in again afterwards: sign-ins made while the app was in Testing still expire after 7 days.

### 4. Start the background service

```bash
python3 split_photos.py ~/scans --upload --install
```

This installs a macOS LaunchAgent. It starts now, restarts if it ever stops, and starts again every time you log in. Any photos already in `~/scans/photos` are uploaded straight away.

That's it. Scan a page and check the **Photo Album Scans** album in Google Photos.

## Using it

- **Scan size:** use **A4**, even on a Letter-sized flatbed. Album pages are usually longer than Letter, and the extra length catches the bottom photo.
- **Resolution and quality:** scan at **600 dpi** with the highest quality setting your scanner has. The photos keep the scanner's full resolution: a 6 × 4 inch print comes out at about 3600 × 2400 pixels at 600 dpi, and only 900 × 600 at 150 dpi.
- **Where things go:**
  - photos: `~/scans/photos/`, named by scan time (`2026-11-15_1527_1.jpg`, `_2`, `_3`, top to bottom)
  - split scans: `~/scans/done/`
  - uploads: the album you chose
- **What it's doing:** `tail -f ~/Library/Logs/photoScanner.log`
- **Sideways photos** are turned upright automatically when they show a face, before they're uploaded. The log shows each one, for example `turned upright: 2026-11-16_0606_1.jpg 90°`. A photo with no clear face, such as a landscape or a building, is left as scanned; rotate it in Google Photos. Auto-rotation only applies to new scans, not to photos already uploaded. To turn it off, reinstall with `--no-rotate`.
- **Dark photos** are brightened automatically. The log lists them, for example `brightened: 2026-11-16_0740_1.jpg`. To turn it off, reinstall with `--no-brighten`.
- **A photo didn't come out, or is cut off:** look in `~/scans/photos/` for a `<scan-time>_PROBLEM.txt` file. One is written for every scan where:
  - a photo-sized area couldn't be extracted
  - a photo is cut off by the scanner's edge
  - a photo was lying mostly off the glass
  - no photos were found
  - the file couldn't be read

  The report says:
  - which scan it was and where that file is now
  - what was extracted
  - where on the page each missed photo is, its size, and why it was skipped
  - what to do about it

  Next to it, `<scan-time>_PROBLEM_boxes.jpg` shows the page with extracted photos boxed in red, cut-off ones in magenta and missed ones in yellow.

  A cut-off photo, meaning one that touches the edge of the scan and is much smaller than the others on the page, is saved as `…_CUTOFF.jpg` so you have it, but it isn't uploaded. Neither are the report and the marked-up page. Delete them once you've rescanned the page or cropped the photo by hand.
- **A page split wrong:** run `python3 split_photos.py ~/scans/done/scan-3.pdf --keep --debug -o /tmp/check` on the scan in question. `/tmp/check` then contains the crops, plus a picture of the page with red boxes around the photos it found.
- **The log says you're not signed in:** run `python3 split_photos.py --photos-login` again.
- **Different album:** reinstall with `--album "Grandma's album"`. The script creates the album the first time it uploads to it.
- **Stop it:** `python3 split_photos.py --uninstall`

### Reliability

- A scan is split only once it has finished arriving, meaning its size has stopped changing. A damaged file is reported once and skipped until it changes.
- Existing files are never overwritten. When a name is taken, the new file gets `-2`, `-3`, and so on.
- A hidden `.uploaded_to_google_photos` file remembers what's been uploaded, so nothing goes up twice. Failed uploads are retried with increasing delays.
- Network and DNS hiccups are retried for up to 5 minutes, both during sign-in and during uploads.

## Running it by hand

You don't have to use the background service:

```bash
python3 split_photos.py ~/scans            # split new scans once
python3 split_photos.py ~/scans --upload   # ...and upload
python3 split_photos.py ~/scans --watch    # keep running in this terminal
```

| Option | What it does |
|---|---|
| `-o DIR` | Save photos to `DIR` instead of `<scans>/photos` |
| `--keep` | Leave scans in place instead of moving them to `done/` (a hidden `.split_photos_done` file remembers which were split) |
| `--debug` | Also save each page with red boxes around the photos found |
| `--no-rotate` | Don't turn sideways photos upright |
| `--no-brighten` | Don't brighten dark photos |
| `--watch` | Keep running and split each new scan as it arrives |
| `--interval N` | Seconds between checks in watch mode (default 5) |
| `--upload` | Also upload photos to Google Photos |
| `--album NAME` | Google Photos album to upload into (default "Photo Album Scans") |
| `--photos-login` | Sign in to Google Photos (always a fresh sign-in) |
| `--install` | macOS: run `--watch` in the background, now and at every login (other options are kept) |
| `--uninstall` | macOS: stop and remove the background service |

## How the splitting works

An album's plastic sleeve is almost colourless, while photos, even pale ones like snow scenes, have some colour. For each page, the script:

1. takes the scanner's own image at full resolution. HP's scan PDFs hold one JPEG, which is used directly with no re-rendering. Other PDFs are rendered at 600 dpi. Scans below 300 dpi are enlarged to 300 dpi, which adds no detail but keeps detection consistent.
2. marks every pixel that is noticeably coloured or dark
3. cleans up that mask and finds each large, solid rectangle, one per photo
4. recovers photos whose shape came out wrong:
   - **A photo that merged with the sleeve's yellowed edge** is separated again with stricter colour thresholds.
   - **A photo with pale sky or snow along a side**, where colour can't tell photo from sleeve, has its box snapped outward to the photo's straight border.
   - A recovered photo that would overlap another photo, or cover more than a third of the page, is rejected and reported instead.
   - **A flip album's pocket stack**, where each print sits in an overlapping pocket and only the top photo of each stack is fully visible, merges with the pockets behind it and the album cover into one big shape. Inside it, each visible photo is found as a rectangle with a long straight edge on all four sides, photo content inside and no pocket edge across it. The strips of the pockets behind it are ignored. A single big print, such as an 8 × 10, has a straight edge all round and is kept whole.
5. straightens slightly tilted photos
6. turns sideways or upside-down photos upright. It tries each quarter turn and keeps the one where a small face detector ([YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet), included in `models/`, runs on your Mac) finds upright faces. A photo with no clear face, or one that already has an upright face, is left as scanned, because leaving a photo alone is better than turning it the wrong way.
7. brightens dark photos. A photo whose typical brightness is below 40% has its black and white points set and its mid-tones lifted. This changes brightness only, not colour, and the lift is capped so a night photo still looks like night. Photos that aren't dark are left exactly as scanned.
8. saves each one as a JPEG

The script finds the photos fresh on every page instead of cutting at fixed positions, so it doesn't matter if the photos sit differently from page to page.

## Limitations

- Several small photos overlapping in one slot (for example school wallet photos) may come out as one image, or be skipped with a `_PROBLEM.txt` report.
- A recovered photo can include a thin strip of sleeve along one edge.
- Black-and-white or very faded photos may be missed, because detection is based on colour. Check with `--debug`.
- Photos touching each other, with no sleeve gap between them, may come out as a single image.
- Sideways photos are only turned when they show a clear face. Landscapes, buildings and photos of people seen from behind or far away stay as scanned; rotate those in Google Photos. Turn auto-rotation off with `--no-rotate`.
- Brightening fixes exposure, not faded colour. A print with a green or orange cast keeps it. Grain in very dark areas becomes a little more visible, and a dark, high-contrast photo can look slightly washed out.
- A photo of someone lying down can be turned so their face is upright and the rest of the scene is on its side.
- Flip-album (pocket) pages were tuned on only two pages of one album, with pink pockets. Only fully visible photos come out; photos tucked under others come out when you flip to them. A photo whose pocket edge has low contrast can be missed or include a strip of pocket, and a photo in a pocket stack that runs off the glass is reported instead of cropped.
- The background service is macOS-only. On Linux, run `--watch` from a systemd user service.

## Files on your Mac

| Path | What it is |
|---|---|
| `~/.config/photoScanner/client_secret.json` | Your Google Cloud Desktop client |
| `~/.config/photoScanner/google_token.json` | Your Google sign-in (delete it to sign out; revoke at [myaccount.google.com/permissions](https://myaccount.google.com/permissions)) |
| `~/.config/photoScanner/google_albums.json` | IDs of the albums the script created |
| `~/Library/LaunchAgents/com.photoscanner.watch.plist` | The background service |
| `~/Library/Logs/photoScanner.log` | Its log |

See [PRIVACY.md](PRIVACY.md) for what the app can and can't access.

## Agent skill layout

```
.agents/skills/photoscanner-setup/
├── SKILL.md                      # overview, splitter, watcher, Google upload, troubleshooting
└── references/
    ├── google-cloud.md           # step-by-step Cloud console setup and its traps
    └── printer-and-mac.md        # printer login, SMB sharing, firewall, scan settings
.claude/skills/photoscanner-setup -> ../../.agents/skills/photoscanner-setup   (link for Claude Code)
```

If you change how setup works, update the skill along with the README, so agents and people get the same instructions.
