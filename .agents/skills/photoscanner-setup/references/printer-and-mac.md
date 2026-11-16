# Printer → Mac: Scan to Network Folder

The setup was done on an HP Color LaserJet MFP M477fdn writing to a Mac over SMB. Other HP LaserJet Pro models behave much the same way.

## Signing in to the printer's web page (EWS)

- The home page at `http://<printer-ip>/` is **public**, so loading it proves nothing about being signed in. Settings pages such as `/hp/device/set_config_folderAddNew.html?tab=Scan&menu=ScantoCfg` are protected.
- The printer uses **HTTP Basic auth**. Its realm says "password only, no username required", so the username is **blank**. Don't suggest `admin`.
- The in-app browser pane never shows the Basic-auth pop-up, and it strips credentials from the URL, so it can't sign in to this printer at all. Use the user's own Chrome. There, they can type `http://:PASSWORD@<printer-ip>/<protected page>` themselves. It has to be a protected page, otherwise the password is never checked.
- To diagnose this, use `fetch()` from a page on the printer's origin and read the `www-authenticate` header.

## Shared folder on the Mac

- **System Settings → General → Sharing → File Sharing (ⓘ):** add the folder (`~/scans`) with Read & Write access for the user. Under **Options…**, turn on "Share files and folders using SMB" and tick the user account, which stores the SMB password hash.
- Check it from Terminal: `sharing -l` lists the share, `netstat -an | grep '\.445 .*LISTEN'` confirms the SMB server is listening, and `dscl . -read /Users/$USER AuthenticationAuthority` should include `SMB-NT`.
- **Firewall:** the problem we hit was the application firewall set to "Block incoming connections" for `/usr/sbin/smbd`. Check it with `/usr/libexec/ApplicationFirewall/socketfilterfw --listapps | grep -A1 smbd`. The user fixes it in System Settings → Network → Firewall → Options by allowing File Sharing / smbd. This is a security setting, so the user changes it, not Claude.

## Printer folder entry

Scan → Scan to Network Folder Setup → **New**:

| Field | Value |
|---|---|
| Display name | e.g. `My Scans` |
| Network path | `\\<mac-ip>\scans` (backslashes; an IP is more reliable than a `.local` name) |
| Username / password | the Mac account's short name (`whoami`) and its login password; the user types the password |

Choose PDF at **600 dpi** with the highest quality / largest file size the printer offers, then **Save and Test**. The script uses the scanner's own image at full resolution, so the scan settings decide the photos' quality. (The first 27 pages of the original album were scanned at 150 dpi by mistake; check a new setup with the resolution check in SKILL.md.)

- If it suddenly stops working, the Mac's IP has probably changed. Suggest a DHCP reservation in the router.
- If the test still fails once the firewall allows smbd, the next suspect is old printer firmware that only speaks SMB1, which macOS no longer supports. Update the printer firmware.

## Scanning album pages

- Set the scan size to **A4**. Album pages are longer than Letter, so the bottom photo gets cut off at Letter size. The M477 glass goes up to A4; Legal only works through the document feeder, and album pages can't go through it.
- The printer names files `scan.pdf`, `scan0001.pdf` and so on, and reuses those names once the folder is empty. The splitter names photos by scan time and moves split PDFs to `done/` with collision-safe names, so reused names are harmless.
- The Mac has to be awake to receive scans. Suggest System Settings → Energy → "Wake for network access".
