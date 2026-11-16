"""Upload split photos to a Google Photos album.

Uses the Google Photos Library API with the "append only" permission:
the script can add photos to your library and create its own album,
but cannot see, change or delete anything else.

Setup (once): put the OAuth "Desktop app" client file from Google Cloud at
~/.config/photoScanner/client_secret.json, then run
    python3 split_photos.py --photos-login
"""
import json
import sys
import time
import warnings
from pathlib import Path

# Older Python/OpenSSL builds make the Google libraries print warnings on
# every run; they don't affect uploading.
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*OpenSSL.*")

API = "https://photoslibrary.googleapis.com/v1"
SCOPES = ["https://www.googleapis.com/auth/photoslibrary.appendonly"]
CONFIG_DIR = Path.home() / ".config" / "photoScanner"
CLIENT_FILE = CONFIG_DIR / "client_secret.json"
TOKEN_FILE = CONFIG_DIR / "google_token.json"
ALBUMS_FILE = CONFIG_DIR / "google_albums.json"
UPLOADED = ".uploaded_to_google_photos"   # ledger kept in the photos folder
BATCH = 50                                 # API limit per batchCreate call


# Photos Google refused; skipped until the watcher restarts.
REJECTED = set()


class NotSignedIn(Exception):
    pass


NET_RETRY_SECONDS = 300   # keep retrying network failures for 5 minutes


def _is_network_error(e):
    """DNS failures, refused/reset connections and timeouts, as raised by
    requests or wrapped by google-auth."""
    import requests
    from google.auth.exceptions import TransportError
    seen = set()
    while e is not None and id(e) not in seen:
        seen.add(id(e))
        if isinstance(e, (requests.exceptions.ConnectionError,
                          requests.exceptions.Timeout, TransportError)):
            return True
        e = e.__cause__ or e.__context__
    return False


def with_retry(fn, what="Google"):
    """Run fn(), retrying network failures (this network has intermittent
    DNS outages) with growing pauses for up to NET_RETRY_SECONDS."""
    deadline = time.time() + NET_RETRY_SECONDS
    delay, warned = 5, False
    while True:
        try:
            return fn()
        except Exception as e:
            if not _is_network_error(e) or time.time() + delay > deadline:
                raise
            if not warned:
                print(f"Can't reach {what} (network/DNS problem); retrying for up to "
                      f"{NET_RETRY_SECONDS // 60} minutes...", flush=True)
                warned = True
            time.sleep(delay)
            delay = min(delay * 2, 30)


def _save_token(creds):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json())
    TOKEN_FILE.chmod(0o600)


def credentials(interactive=False, force=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if TOKEN_FILE.exists() and not force:
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.refresh_token:
        try:
            with_retry(lambda: creds.refresh(Request()))
            _save_token(creds)
            return creds
        except Exception as e:
            if _is_network_error(e):
                raise   # still offline after retrying; not a sign-in problem
            if not interactive:
                raise NotSignedIn(f"Google sign-in expired ({e}); "
                                  "run: python3 split_photos.py --photos-login")
    if not interactive:
        raise NotSignedIn("not signed in to Google Photos; "
                          "run: python3 split_photos.py --photos-login")
    if not CLIENT_FILE.exists():
        sys.exit(f"Missing {CLIENT_FILE}\nDownload the OAuth Desktop client JSON "
                 "from Google Cloud and save it there (see README).")
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
    # The final step (swapping the browser's code for a token) is the one a
    # DNS hiccup breaks; retry it rather than making you sign in again.
    fetch = flow.fetch_token
    flow.fetch_token = lambda **kw: with_retry(lambda: fetch(**kw), "Google sign-in")
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    _save_token(creds)
    return creds


def login():
    # Always do a fresh browser sign-in, e.g. after publishing the app,
    # switching accounts, or when the old sign-in was revoked.
    credentials(interactive=True, force=True)
    print(f"Signed in. Token saved to {TOKEN_FILE}")


def _session():
    from google.auth.transport.requests import AuthorizedSession
    return AuthorizedSession(credentials())


def _albums():
    return json.loads(ALBUMS_FILE.read_text()) if ALBUMS_FILE.exists() else {}


def album_id(sess, title):
    """ID of the album this script created with that title (creating it once)."""
    albums = _albums()
    if title in albums:
        return albums[title]
    r = with_retry(lambda: sess.post(f"{API}/albums", json={"album": {"title": title}}))
    r.raise_for_status()
    albums[title] = r.json()["id"]
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ALBUMS_FILE.write_text(json.dumps(albums, indent=2))
    print(f'Created Google Photos album "{title}"', flush=True)
    return albums[title]


def _forget_album(title):
    albums = _albums()
    if albums.pop(title, None) is not None:
        ALBUMS_FILE.write_text(json.dumps(albums, indent=2))


def not_uploaded(folder: Path):
    ledger = folder / UPLOADED
    done = set(ledger.read_text().splitlines()) if ledger.exists() else set()
    return [p for p in sorted(folder.glob("*.jpg"))
            if p.name not in done and p.name not in REJECTED
            and "_boxes" not in p.stem]


def upload_pending(folder: Path, album: str):
    """Upload every photo in `folder` not uploaded yet; returns how many."""
    todo = not_uploaded(folder)
    if not todo:
        return 0
    sess = _session()
    aid = album_id(sess, album)
    count = 0
    for i in range(0, len(todo), BATCH):
        items = []
        for p in todo[i:i + BATCH]:
            data = p.read_bytes()
            r = with_retry(lambda: sess.post(f"{API}/uploads", data=data, headers={
                "Content-Type": "application/octet-stream",
                "X-Goog-Upload-Content-Type": "image/jpeg",
                "X-Goog-Upload-Protocol": "raw",
            }))
            r.raise_for_status()
            items.append((p, r.text))
        body = {
            "albumId": aid,
            "newMediaItems": [{"simpleMediaItem": {"uploadToken": t, "fileName": p.name}}
                              for p, t in items],
        }
        r = with_retry(lambda: sess.post(f"{API}/mediaItems:batchCreate", json=body))
        if r.status_code in (400, 403, 404) and "album" in r.text.lower():
            _forget_album(album)   # album was deleted; recreate on next try
        r.raise_for_status()
        ok = []
        for (p, _), res in zip(items, r.json().get("newMediaItemResults", [])):
            status = res.get("status", {})
            if status.get("code", 0) == 0:
                ok.append(p.name)
            else:
                REJECTED.add(p.name)
                print(f"{p.name}: Google Photos rejected it ({status.get('message')})",
                      flush=True)
        if ok:
            with open(folder / UPLOADED, "a") as fh:
                fh.write("".join(n + "\n" for n in ok))
            count += len(ok)
    print(f'Uploaded {count} photo(s) to Google Photos album "{album}"', flush=True)
    return count
