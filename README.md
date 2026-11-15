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
| `--keep` | Leave scans in place instead of moving them to `done/` |
| `--debug` | Also save a copy of each page with red boxes around the photos found |

## Tips for scanning

- Use **A4** scan size on a Letter-sized flatbed — the extra length usually captures the bottom photo of an album page.
- Photos stored sideways in the album come out sideways; rotate them afterwards.

## Limitations

- Black-and-white or very faded photos may be missed, since detection is colour-based. Use `--debug` to check.
- Photos touching each other with no sleeve gap between them may come out as one image.
