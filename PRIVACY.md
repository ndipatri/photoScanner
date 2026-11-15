# photoScanner privacy policy

photoScanner is a personal script that runs entirely on your own computer.

- **What it accesses:** only the Google Photos permission "Add to your Google Photos library" (`photoslibrary.appendonly`). It uploads photos you scanned into your own library and creates an album for them. It cannot see, download, change or delete anything else in your Google Photos.
- **What it stores:** your Google sign-in token and the ID of the album it created, in `~/.config/photoScanner/` on your computer. Nothing is stored anywhere else.
- **What it shares:** nothing. No data is sent to the author or any third party; photos go only from your computer to your own Google Photos account.
- **Removing access:** delete `~/.config/photoScanner/google_token.json`, and revoke access at [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

Questions: open an issue at [github.com/ndipatri/photoScanner](https://github.com/ndipatri/photoScanner/issues).
