# RoomRoller

Try paint colors on a photo of a room before buying a can. Sign in with Google to keep rooms in your collection.

## What you can do

1. Open a room photo, paste one, or drop it on the window. A built-in sample room is there if you want to click around first.
2. The photo opens with the walls, ceiling, and floor marked. Each unpainted surface is tinted so you can tell them apart. Use Dots if you want to add another wall yourself.
3. Click a surface and choose a color. Shadows, light falloff, and a bit of the wall's texture stay in place, so the result reads as paint rather than a flat sticker.
4. Set the sheen (matte, eggshell, satin, semi-gloss), how fully the color covers, and a shade nudge lighter or darker than the chip.
5. If a wall was missed or a sofa was included, fix the mask. The wand selects up to a hard edge such as a corner. The brush and eraser paint the mask directly. Hold Shift with the wand to add to the surface you already have selected.
6. Drag Before / After to compare with the original photo, then download a PNG.
7. Save puts the photo, the walls, and the colors into your collection. Saving asks you to sign in with Google first, and the collection belongs to that account: sign in again later, from any browser, and the rooms are there. Open Collection to change the colors again.

## Run

From this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

Open http://localhost:8765

Or run `.\run.ps1`, which does the same thing.

Photos are decoded in this process and scaled so the long edge is at most 1400 pixels. A photo only leaves the editing session when you save it to your collection.

## Google sign-in

Sign-in needs an OAuth client ID from Google. It is free and takes a few minutes.

1. Open https://console.cloud.google.com/apis/credentials and choose a project (or create one).
2. If asked, set up the consent screen: External, app name RoomRoller, your email as support and developer contact.
3. Create credentials > OAuth client ID > Web application.
4. Under Authorized JavaScript origins add `http://localhost`, `http://localhost:8765`, and the published app's address (for example `https://roomroller.replit.app`). No redirect URIs are needed.
5. Copy the client ID. For local runs, put it in a `.env` file in this folder:

   ```
   GOOGLE_CLIENT_ID=1234567890-abc.apps.googleusercontent.com
   ```

   On Replit, add `GOOGLE_CLIENT_ID` and `SESSION_SECRET` (any long random string) under Secrets, and add them to the deployment's secrets too.

Open the app at `http://localhost:8765` rather than `127.0.0.1`; Google only accepts `localhost` for local sign-in. Without a client ID the studio still works, but Save and Collection are unavailable.

## Where saved rooms are kept

Rooms are filed under the Google account's id. On Replit they go to Replit Object Storage, which survives redeploys; create a bucket once from the Object Storage tool in the workspace. Running anywhere else, they go to `data/collection/users/` on that computer. Set `ROOMHUE_STORAGE=disk` or `ROOMHUE_STORAGE=replit` to choose explicitly. If the published app can't find the default bucket, add a `ROOMHUE_BUCKET_ID` secret with the bucket's ID from the Object Storage tool.

## How a photo becomes paint

**Surfaces.** A photo is marked when it opens. The detector groups the picture into walls, ceiling, and floor, and leaves out small bright spots and furniture when it can. Dots still lets you add a wall by placing a point at each corner. The wand and brush can adjust a wall after it exists.

The entry point is `detect_surfaces(image)`. A learned segmenter can replace the body of that function later without changing the studio.

**Wand.** A click flood-fills across gradual light changes, which is what a single wall looks like, and stops at hard edges so it does not cross a corner.

**Paints.** Color search uses published books for Sherwin-Williams, Behr, Benjamin Moore, PPG, Valspar, Glidden, Dunn-Edwards, Farrow & Ball, Kilz, and Dutch Boy. Sherwin-Williams, Behr, Valspar, and Glidden come from each brand's own site; colors a brand has since dropped stay in the list marked archived. Benjamin Moore is taken from their color pages. Hex values are the published ones, and a custom hex still works. To refresh, run `python scripts/fetch_sources.py` and then `python scripts/build_catalog.py`.

**Color.** The masked pixels are converted to Lab. Their median lightness moves most of the way toward the paint chip, while the pattern of light and dark on the wall is kept. Color (a/b) moves to the chip, with a little of the wall's own variation left so the surface does not go flat. Coverage blends that result back toward the photo. Sheen brightens the spots that were already lighter than the wall. The same constants live in `app/color_math.py` and `static/app.js`. The download is the picture you see in the browser.

## What this version is not

These are the next layers, in the order they earn their place. The studio is already shaped around surfaces, masks, and per-surface finish, so they can be added without a rewrite.

- A learned indoor segmenter behind `detect_surfaces`, for rooms where color alone cannot separate a wall from a cabinet.
- More classes: trim, doors, cabinets, and tile, each with its own mask and finish.
- Product lines and the sheen printed on a specific can, on top of the color books already in search.
- Time-of-day lighting and white balance, so a north-facing room and a lamp-lit room do not share one exposure.
- A phone camera path, and a way to send someone the before/after without handing over the project file.

## Privacy and legal

The Privacy Policy, Terms of Service, and Cookie Policy are at `/privacy`, `/terms`, and `/cookies` (`static/legal/`). Requests go to privacy@roomroller.com. Sign-in requires agreeing to the current version in `app/auth.py` (`LEGAL_VERSION`); bump it, and the dates on the pages, when either policy changes materially. Anyone signed in can delete their account and saved rooms from the menu, the Collection window, or the Privacy page. `docs/privacy-audit.md` lists what is stored, every third-party service and dependency, and what to re-check when something is added.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The sample room is a drawn scene with a known ceiling, floor, three walls, a window, a picture, and a sofa. Tests check that the detector finds the paintable surfaces, leaves the sofa and window alone, and that the wand stays on the wall you click.
