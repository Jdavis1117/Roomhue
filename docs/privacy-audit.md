# Privacy, consent, and third-party audit

Last reviewed: October 9, 2026. Re-check this whenever a dependency, cookie, or stored field is added.

The public policies live at `/privacy`, `/terms`, and `/cookies` (files in `static/legal/`). Contact: privacy@roomroller.com, forwarded by Namecheap.

## Data inventory

| Data | Where it lives | Why | Kept for |
|---|---|---|---|
| Opened photo (pixels only) | Server memory (`_sessions` in `app/main.py`) | Find walls, render preview | 2 hours idle, or fewer when 6+ photos are open |
| Saved room: photo PNG, thumbnail, wall masks, colors, name, time | Replit Object Storage, `users/<google id>/<room id>/` | Collection | Until the room or account is deleted |
| Google account ID and first name | Signed session cookie `roomhue_session` | Know whose collection it is; show name in menu | 60 days or sign-out |
| Agreement record (`terms_version`, `accepted_at`, `first_accepted_at`) | `users/<google id>/account.json` | Proof of consent to Terms and Privacy Policy | Until account deletion |
| Cookie choice | `localStorage["roomroller-consent"]` | Don't ask every visit | Until cleared |
| Recent colors, panel width | `localStorage["roomhue-recent"]`, `["roomhue-panel-width"]` | Preferences | Only written if Preferences are allowed |
| Request logs (IP, time, path) | Replit hosting | Operations and security | Replit's retention |

Not collected: email address, profile photo, contacts, location, EXIF/camera metadata, analytics, advertising IDs.

## Minimization changes made in this review

- Sign-in keeps only `sub` (account ID) and given name. Email and profile picture are no longer stored, and older session cookies that still hold them are trimmed on read (`auth.current_user`).
- Google's sign-in script is no longer on every page. It loads only after the visitor opens sign-in and ticks the agreement box, so visitors who never sign in make no requests to Google.
- Preference storage is opt-in through the consent banner. Choosing "Essential only" clears previously saved preferences.
- Saved photos are re-encoded from pixels, so EXIF location and camera data never reach storage. Large photos are also shrunk and re-encoded in the browser before upload.

## Consent points

| Where | What is agreed | How it's recorded |
|---|---|---|
| Cookie banner (first visit; "Cookie settings" link anywhere) | Optional preference storage | `localStorage["roomroller-consent"]` with timestamp |
| Sign-in dialog checkbox | Terms of Service and Privacy Policy, version `auth.LEGAL_VERSION` | Server refuses sign-in without it (`POST /api/auth/google` needs `accepted_terms` and the current `terms_version`); stored in `account.json` |
| Sign-in dialog notice | Loading Google's sign-in service and its cookies | Google is loaded only after the checkbox is ticked |
| Start screen notice | How opened photos are processed | Notice, with a link to the Privacy Policy |

When the Terms or Privacy Policy change materially, bump `LEGAL_VERSION` in `app/auth.py` and the dates in `static/legal/*.html`. Everyone is then asked to agree again at their next sign-in.

## Data deletion

- Self-service: Menu → **Delete account and data** (phones), Collection → **Delete account and data** (desktop), or the button on `/privacy#delete`. Calls `DELETE /api/account`, which removes every object under `users/<google id>/` and signs out.
- By email: privacy@roomroller.com. Respond within 30 days.

## Third-party services

| Service | Used for | Data it receives | Loaded when |
|---|---|---|---|
| Google Identity Services (`accounts.google.com/gsi/client`) | Sign in with Google | The sign-in itself; Google may set `g_state` and google.com cookies | Only after the visitor ticks the agreement box |
| Google token verification (`google-auth`, server side) | Check the ID token | Google's public signing keys are fetched; no user data is sent | At sign-in |
| Replit hosting | Runs the app | All requests, opened photos in memory, logs | Always |
| Replit Object Storage (Google Cloud Storage) | Saved rooms and agreement records | Saved rooms | Save, open, delete |

No analytics, advertising, error-tracking, font, or CDN services are used. The page loads nothing from other domains until sign-in.

## Open-source dependencies and licenses

| Package | License | Notes |
|---|---|---|
| fastapi, pydantic | MIT | Web framework, validation |
| starlette, uvicorn, httpx, itsdangerous | BSD-3-Clause | Server, sessions, test client |
| numpy | BSD-3-Clause (with 0BSD, MIT, Zlib parts) | Image math |
| pillow | MIT-CMU | Image decoding |
| opencv-python-headless | Apache-2.0 | Wall detection |
| python-multipart, requests, google-auth, google-cloud-storage | Apache-2.0 | Uploads, Google token check, storage |
| replit-object-storage | ISC | Storage client |

All are permissive and allow commercial use. Apache-2.0 packages require keeping their license and NOTICE files when redistributing them; RoomRoller installs them from PyPI and does not redistribute them.

## Fonts and images

- Fonts: system fonts only (Segoe UI, Palatino, Georgia, system-ui). No font files are bundled or downloaded, so there is nothing to license.
- Images: the favicon/logo is an inline SVG drawn for RoomRoller; the sample room is generated in code (`app/sample_room.py`). No stock or third-party images.
- Paint data: names, numbers, and color values published by each brand. Names and numbers are the brands' trademarks, used only to identify their colors. The Terms state that RoomRoller is not affiliated with any brand.

## Accessibility

Checked with axe-core 4.10 on desktop and phone layouts (start screen with the cookie banner, the editor, the sign-in dialog) and on all three policy pages: no violations. Changes made in this review:

- The photo canvas has `role="img"` with a label; collection thumbnails have alt text with the room name; decorative elements are hidden from screen readers.
- Dialogs have `role="dialog"`, `aria-modal`, and a label. Focus moves into them and returns afterwards, and Escape closes them.
- Buttons that were nested inside fold headers ("Add wall", "Clear color") moved into the fold bodies.
- The color grid is a labeled group instead of a listbox; the panel resize handle reports its value.

## Still to do outside the code

- Google Cloud → Google Auth Platform → **Branding**: set the app name to RoomRoller, the home page to `https://roomroller.com`, the privacy policy to `https://roomroller.com/privacy`, the terms to `https://roomroller.com/terms`, and add `roomroller.com` as an authorized domain. Google requires these before the app can be published beyond test users.
- Choose a governing-law state for the Terms, if one is wanted.
- Have the policies reviewed by a lawyer before relying on them.
