# Brand icons

`build-icons.mjs` renders the favicons, app icons and web manifest from the source SVGs in
`docs/brand/` into `docs/brand/icons/`. The generated files are committed, so building the
app never needs this toolchain. Re-run it only when a source SVG changes.

It rasterises with Playwright's headless Chromium:

```bash
# with playwright-core installed in this repo
npm install --no-save playwright-core && npx playwright-core install chromium-headless-shell
node scripts/brand/build-icons.mjs

# or point it at an existing install (e.g. a sibling checkout that already has it)
PLAYWRIGHT_CORE=../wiedisync/node_modules/playwright-core/index.mjs node scripts/brand/build-icons.mjs
```

| Source | Output in `docs/brand/icons/<vb|bb>/` |
|---|---|
| the ball from `wieplay-icon-<sport>.svg`, alone on a transparent square | `favicon.svg`, `favicon.ico` (16/32/48): the dot trail is unreadable at tab size |
| `wieplay-icon-<sport>.svg` | `icon-192.png`, `icon-512.png` |
| same, corners removed (full bleed) | `icon-maskable-192.png`, `icon-maskable-512.png`, `apple-touch-icon.png` (180) |
| `wieplay-mark-<sport>.svg`, trail dots recoloured to `#EEF1F5` | `mark-on-dark.svg` (for navy / dark-mode backgrounds) |

`site.webmanifest` uses the `vb` icons (the default variant); basketball pages swap only the
favicon link.
