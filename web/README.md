# Nomal web

The browser app for Nomal: React 19, TypeScript, Vite, Tailwind CSS v4, React Router 7
(library mode), TanStack Query 5 and i18next (English, German). See `../docs/PLAN.md` §13 for
the brand and UI rules this follows.

## Run it

Needs Node 24.15 or newer.

```sh
npm ci
npm run dev        # http://localhost:5173
```

Vite proxies `/api` to the backend at `http://127.0.0.1:8077` (start it from `../backend`). To
use another address, set `NOMAL_API_URL`, e.g. `NOMAL_API_URL=http://127.0.0.1:8001 npm run dev`.

The proxy keeps the `Host` header, so the backend sees the dev server's origin. Configure the
backend's public URL as `http://localhost:5173` in dev, so the login redirect chain through
Wiedisync and the session cookie come back to the Vite server.

In production the API serves the built files from `dist/` on the same origin. Paths that are
not `/api/...` or a file in `dist/` must return `index.html` (client-side routes such as
`/teams/92`).

## Commands

| Command | What it does |
|---|---|
| `npm run dev` | Dev server with the `/api` proxy |
| `npm run build` | Type-check (`tsc -b`) and build into `dist/` |
| `npm run preview` | Serve `dist/` locally, with the same proxy |
| `npm run lint` | ESLint (typescript-eslint strict, react-hooks, react-refresh) |
| `npm test` | Vitest once (jsdom); `npm run test:watch` to keep it running |
| `npm run sync-brand` | Copy the brand assets from `../docs/brand/` into `public/` |

## Brand assets

The logo marks and icons are generated in `../docs/brand/` (see `../scripts/brand/`). After
they change, run `npm run sync-brand` and commit the copies in `public/`:

- `icons/vb/*`, `icons/bb/*`, `icons/site.webmanifest` go to `public/icons/`.
- `nomal-mark-vb.svg`, `nomal-mark-bb.svg` and the two `mark-on-dark.svg` files go to
  `public/brand/` (the dark ones as `nomal-mark-vb-dark.svg` and `nomal-mark-bb-dark.svg`).

The wordmark is the text "nomal" in Bricolage Grotesque ExtraBold, self-hosted through
`@fontsource/bricolage-grotesque` (Latin subset, weight 800 only).

## How the UI is put together

- **Colours** are tokens in `src/index.css`. Tailwind's default palette is switched off;
  components use the semantic tokens (`bg-page`, `bg-surface`, `text-fg`, `text-muted`,
  `bg-accent`, `text-on-accent`, `text-link`, `border-hairline`, `outline-ring`, `text-danger`).
- **Sport accent**: `data-sport="vb|bb"` on `<html>` (default `vb`). The team page sets it from
  the team with `useSportAccent()` (`src/lib/sport.ts`), which also swaps the favicon and the
  header mark. Volleyball uses blue fills with white text in light mode and yellow in dark mode;
  basketball uses orange fills with navy text. Yellow and orange are never text on light.
- **Dark mode** follows `prefers-color-scheme` until the user picks one with the header toggle;
  the choice is kept in `localStorage` (`nomal-theme`). Language: `nomal-lang`, English default.
- **Formats**: `src/lib/format.ts` (`formatDate` → `18.10.2026`, `formatTime` → `14:00`, locale
  `de-CH`, 24 h). API dates and times are shown as given; timestamps in Zurich time.
- **Data**: `src/lib/api.ts` is the fetch client. It throws `ApiError` subclasses
  (`UnauthorizedError`, `ForbiddenError`, `NotFoundError`). `GET /api/me` answering 401 means
  logged out; a 401 anywhere else invalidates the `me` query, which brings back the login
  screen. Login is a navigation to `/api/auth/login?next=…`, never a fetch. Query hooks live in
  `src/lib/queries.ts`.
- **Components** (`src/components/`): `Button` (44 px tall on phones, 36 px from `sm`), `Card`
  (rounded-2xl, hairline border), `Table` (lists of records are always tables; hide secondary
  columns on phones with `hidden sm:table-cell`), `Badge`, `Spinner`, `Switch` and the toast
  provider. No native `alert`/`confirm`/`prompt` (ESLint `no-alert`); ask with `ConfirmDialog`.
- **Uploads** (`src/lib/upload.ts`): S3 multipart straight from the browser to the bucket. Parts
  of all files share 4 parallel PUTs (XMLHttpRequest, for progress); part URLs are signed by the
  API a few parts ahead, so none expires in a long queue; each part is retried with back-off, and
  a 403 signs it again. An interrupted upload resumes on the video page: the user picks the same
  files, and parts already stored are skipped. The bucket's CORS rules must allow `PUT` from the
  app's origin.
- **Playback** (`src/components/VideoPlayer.tsx`): HLS from `/api/media/{id}/master.m3u8` with
  the hls.js light build, loaded only on the video page; native HLS where Media Source is missing
  (older iPhones). Segments are 302 redirects to short-lived storage URLs, so the bucket's CORS
  rules must also allow `GET` from the app's origin.
- Text is English first, in sentence case. German strings are in `src/i18n/de.ts` (Swiss
  spelling, informal "du"); the type of `de` makes both files keep the same keys.

## Dependencies

Versions are pinned exactly (`.npmrc` has `save-exact`). When upgrading, prefer releases that
have been public for at least a week. TypeScript stays on 6.0 until typescript-eslint supports
a newer major.
