# site tools

`../site_palette.py` reads a live site's colour roles (`extract`), recolours the site with brand-identity palettes
(`apply`), and reads competitor sites (`competitors`). The skill calls it through `../site_preview.py`
(`brand.py site extract|competitors|apply`).

`site_engine.js` (this folder) holds the measuring and recolouring JavaScript: page functions that run inside the
site's page (`pageExtract`, `pageRecolor`, `pageFontSwap`, `pageLogoSwap`, ...) and pure functions (`buildRoles`,
`layoutStress`, ...) that run in a blank engine page of the same browser. `../cdplib.py` drives the browser over the
Chrome DevTools protocol (standard library only; pipe transport on Linux and macOS, websocket on Windows).

`apply --identity sets/X/identity.json` puts a whole identity set on the site: palette colours, the set's display and
text faces (from `type/fonts.json`, loaded from bytes with `FontFace`, `size-adjust` matched to the old face's x-height
measured in the page), and the header logo (`logo/build/full-color.svg`, cropped to its ink and fitted inside the old
logo box +10%). `apply.json` records `layoutStress`: elements that overflow or gain lines after the swap. Kept or
absent components stay as the site has them.

Needs Python and a Chromium-based browser only: `BRAND_IDENTITY_BROWSER` (or `CHROMIUM_PATH`), the Playwright cache
(`~/.cache/ms-playwright`), or an installed Chrome / Chromium / Edge / Brave. `python3 site_palette.py check` runs a
self-test on `selftest.html`. No browser: preview on mock pages instead (`site_preview.py --mock landing,app ...`).
