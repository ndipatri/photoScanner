# Google Cloud / Google Photos setup

This is a one-time setup that gives the script its own OAuth "Desktop app" login. Every step is in https://console.cloud.google.com. The user has to be signed in to Google in the browser you use.

**Pick the browser carefully.** The in-app browser pane loads the console, but several saves silently failed there: test users never saved, and the console showed "Failed to load" errors. If a save doesn't stick, check by reloading the page. If it still doesn't stick, have the user do that step in their own Chrome (or use the Claude in Chrome extension if it's connected).

## Existing setup (as of 2026-11-15)

- Project: `photoScanner`, ID `photoscanner-514821`. Billing is linked to the user's existing billing account because the create form required one. The Photos Library API is free.
- Photos Library API: enabled
- Google Auth Platform → Branding: app name `photoScanner`, support and developer contact set to the user's own account, home page `https://github.com/ndipatri/photoScanner`, privacy policy `https://github.com/ndipatri/photoScanner/blob/main/PRIVACY.md`, authorized domain `github.com`
- Audience: External, **In production** (published). The user's account is also a test user.
- Data Access: `.../auth/photoslibrary.appendonly` (listed under "sensitive")
- Clients: Desktop app "photoScanner desktop". Its JSON is at `~/.config/photoScanner/client_secret.json` (mode 600).

If all of that still exists, a new Mac only needs `client_secret.json` copied over (or a new client created) followed by `--photos-login`.

## Steps from scratch

1. **Create the project.** Go to `/projectcreate` and name it `photoScanner`. The project ID gets a numeric suffix; read it from the notification afterwards. A billing account field may be required. Linking one is fine, since the API costs nothing.
2. **Enable the API.** Go to `/apis/library/photoslibrary.googleapis.com?project=<ID>` and click **Enable**.
3. **Branding / consent screen.** Go to `/auth/overview/create?project=<ID>`. It's a 4-step wizard: App name, then support email (pick from the dropdown), then Audience **External**, then developer contact email, then Finish, then the "I agree to the Google API Services: User Data Policy" checkbox, then **Create**. **Ask the user before ticking the agreement.**
   - In this console, the first click on "Next" often only scrolls the page. Take a screenshot after each click and click again if the step didn't advance.
4. **Scope.** Go to `/auth/scopes?project=<ID>`, click **Add or remove scopes**, and scroll to "Manually add scopes". Paste `https://www.googleapis.com/auth/photoslibrary.appendonly`, click **Add to table**, then **Update**, then **Save**. Wait for "Data access changes saved!".
5. **Desktop client.** Go to `/auth/clients/create?project=<ID>`, choose Application type **Desktop app** and the name `photoScanner desktop`, then **Create**.
   - **The client secret is only shown in the dialog that appears after Create.** Don't close it. Ask the user before downloading, then click **Download JSON**. The file goes to `~/Downloads/client_secret_<id>.apps.googleusercontent.com.json`. Move it to `~/.config/photoScanner/client_secret.json` and `chmod 600` it. If the dialog gets closed first, create a new client.
6. **Publish (avoids the weekly sign-in expiry).** While the app is in "Testing", Google expires refresh tokens after **7 days**, and only listed test users can sign in. To publish:
   - "Publish app" on `/auth/audience` stays greyed out ("you must complete your configuration on the Branding page") until Branding has an **Application home page**, a **privacy policy link** and a matching **authorized domain**. A GitHub repo URL, a `PRIVACY.md` in that repo, and the domain `github.com` all work.
   - Then **Publish app → Confirm**. The status becomes "In production", with a banner saying the app "requires verification". Verification is **not** needed for personal use (under 100 users); the only effect is the "unverified app" warning at sign-in.
   - A token issued while the app was in Testing keeps its 7-day expiry. **Sign in again after publishing.** `--photos-login` always does a fresh sign-in for this reason.
7. **Test users (only if staying in Testing).** On `/auth/audience`, click **Add users**, enter the address, press **Return** so it becomes a chip (don't type a trailing comma, which gets flagged as invalid), then **Save**. Reload the page and check that "1 user (1 test…)" appears.

## Sign-in

```bash
cd ~/photoScanner && python3 split_photos.py --photos-login
```

This starts a local redirect server on a random localhost port and opens the browser. The user picks the account, clicks **Advanced → Go to photoScanner (unsafe)**, and allows "Add to your Google Photos library". The browser then shows "The authentication flow has completed", and the terminal prints `Signed in. Token saved to …`. If the terminal printed "Signed in" without ever showing a URL, no new sign-in happened; that was a bug in older versions, which reused the existing token.

Check the token without printing secrets:
```bash
python3 -c "import json,os;d=json.load(open(os.path.expanduser('~/.config/photoScanner/google_token.json')));print(bool(d.get('refresh_token')), d.get('scopes'))"
```

## Notes on the API

- Since the March 2025 Photos Library API changes, apps can only manage media and albums **they created**. Append-only upload into an app-created album is still fully supported, and that's all this project does.
- Upload is two steps: POST the bytes to `/v1/uploads` to get an upload token, then call `/v1/mediaItems:batchCreate` with up to 50 items and the `albumId`.
- If the user deletes the album in Google Photos, `batchCreate` returns an album error. The code forgets the cached album ID and creates the album again on the next attempt.
- Drive for desktop's "Back up to Google Photos" option is discontinued. Don't suggest it.
