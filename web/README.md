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
  components use the semantic tokens (`bg-page`, `bg-surface`, `bg-sunken`, `text-fg`,
  `text-muted`, `bg-accent`, `text-on-accent`, `bg-accent-soft` with `text-on-accent-soft` for
  selected and current states, `text-link`, `border-hairline`, `border-hairline-strong`,
  `bg-hover`, `outline-ring`, `text-danger`), the shadows `shadow-card` and `shadow-raised`
  (hover), and the radii `rounded-control` (buttons, inputs, nav) and `rounded-card` (cards).
  Text links use the `link` utility: the link colour with a light underline that firms up on
  hover (in basketball's light mode the link is navy with an orange underline).
- **Layout**: every page uses the window up to `max-w-[110rem]` with 16/24/32 px gutters; forms
  (upload, reel builder) keep a readable width of their own. The app bar is navy in both themes
  (`.appbar` switches its tokens to the dark ones) with the mark-on-dark; from `lg` the menu is
  in the bar, below that it is a row of tabs under it. Page tops use `PageHeader` (optional line
  above the title, title, subtitle, actions), sections `SectionHeader`. The video page and the
  reel player put the list beside the player from `xl`.
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
- **Components** (`src/components/`): `Button`/`ButtonLink` (`primary`, `secondary`, `ghost`,
  `danger`, `ghostDanger`; 44 px tall on phones, 36 px from `sm`; `buttonClasses()` gives the
  same look to a file input's label or a plain download link), `Card` (card radius, hairline
  border, soft shadow), `Table` (lists of records are always tables; tinted header, rows light
  up on hover; hide secondary columns on phones with `hidden sm:table-cell`), `Badge`, `Chip`,
  `Message` (notices, with an optional icon), `EmptyState` (icon, title, a helpful line, an
  action), `VideoPoster`, `Spinner`, `Switch` and the toast provider. No native
  `alert`/`confirm`/`prompt` (ESLint `no-alert`); ask with `ConfirmDialog`.
- **Start page** (`src/pages/HomePage.tsx`, `src/pages/home/`): a greeting, the actions (upload
  for coaches, team managers and sport admins, as `mayUpload` in `src/lib/teams.ts` mirrors the
  API), a card per team (its newest season; earlier seasons behind a disclosure) with the
  poster of its latest ready video, and the newest videos of those teams. The videos come from
  one `GET /api/teams/{id}/videos` per team (newest season and the one before).
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
- **Skip dead time** (`src/components/RallyPanel.tsx`, `src/lib/condensed.ts`): on the video
  page, when a video has rallies, everyone who can watch it can play only the rallies (2 s
  before, 1.5 s after, overlapping windows merged), jump through a rally list with the score,
  and narrow it by player, skill, grade and set.
- **Tagging workspace** (`/videos/:id/tag`, `src/pages/tag/`, loaded on demand, desktop only;
  smaller screens get the review of suggestions only):
  - `src/lib/grammar.ts` parses entries like `7A#`, `a12S=`, `3A#46` for the live preview.
  - `src/pages/tag/controller.ts` is the keyboard (PLAN §9.2) as plain TypeScript, so fast
    typing never sees stale React state; `?` in the app lists every key.
  - `src/lib/taggingStore.ts` shows the server's data with the queued changes on top, keeps
    undo/redo of this page's own changes, and holds the rally being played (the API needs an
    end time, so a rally is sent when it ends; the server links tags to rallies by time).
  - `src/lib/outbox.ts` is the IndexedDB outbox (PLAN §9.6): every change is stored first,
    coalesced per id, sent in batches to `POST /api/videos/{id}/changes` after 1 s, retried
    with back-off, replayed after a reload. Each change names the server version it was based
    on (`base_version`); changes the server reports as `stale` are dropped and shown. A 422 is
    narrowed down to the one bad change by splitting the batch.
  - `src/lib/rallies.ts` mirrors the server's scoring for the moments when changes are still
    queued; after each sync the server's scores are shown.
- **Nomal's rally suggestions and their review** (PLAN §10.1, §11; `src/lib/suggestions.ts`,
  `src/lib/analysis.ts`, `src/pages/tag/review.ts`, `ReviewPanel.tsx`, `RallyList.tsx`): the
  analysis proposes rallies as segments with `source: "model"`, a `confidence` and a `review`
  state. Open ones (`unreviewed`) are outlined (dashed) on the timeline and carry a "Suggested ·
  87%" chip in the rally list (filter "Suggestions (n)"); accepted and corrected ones are rallies
  with a small marker. They are not shown as rallies on the video page until reviewed.
  - Review mode (`/videos/:id/tag?mode=review`, the only mode on tablets and phones) goes through
    the open ones in time order and loops each from 1 s before it (`src/lib/useLoopPlayback.ts`):
    `Enter` accept, `X` reject, `←/→` start and `Shift+←/→` end by 0.25 s (`Alt`: one frame), `M`
    merge with the next, `S` split at the playhead, `U` undo, `↑/↓` move, `Esc` back to tagging.
    Every answer is an ordinary segment change through the outbox: accept sets `review:
    "confirmed"`, a moved boundary `"corrected"`, reject deletes with `review: "rejected"` (kept by
    the server as a negative); merge corrects the first and rejects the second; a split adds a
    manual rally that comes next in the list. "Accept all remaining with confidence ≥ 90%" asks
    first and is one undo step.
  - `AnalysisStatus` (video page and tagging page, for those who may tag, volleyball): the
    analysis state from `GET /api/videos/{id}/analysis` (polled while queued or running),
    "Analyse" (`POST …/analyse`) when there is none or it failed, the F1 against the coach's own
    rallies, and "Rallies reviewed" (`POST …/rallies-reviewed`), which makes Nomal learn from the
    game; it waits until no suggestion is open, and asks for it once all are answered.
- **Clips, reels, comments, downloads** (M3; API calls and hooks in `src/lib/library.ts`):
  - A clip is a time range. On the video page "Clip in" / "Clip out" mark it at the playhead
    (`[`, `]`, then `C` in the tagging workspace; `Shift+C` still sets the selected tag's clip
    window). Playing a clip seeks to its start and stops at its end, or moves on to the next
    one (`src/lib/useRangePlayback.ts`, `src/lib/clips.ts`). "My clips" (`/clips`) opens
    `/videos/:id?clip=…`, which plays just that clip. Sharing with the team is offered only to
    those the API allows (`can_tag` or `can_upload`); a 403 is still explained.
  - Reels (`/reels`): a smart reel is a saved query; the builder (`/reels/new`) previews it
    with `POST /api/reels/preview` 300 ms after the last change. Manual reels are made from
    my clips, or with "Add to reel" on a clip or a tag row. The reel player
    (`src/components/ReelPlayer.tsx`) plays the pieces across videos with two `<video>`
    elements: one on screen, the other loading the next piece's video
    (`src/lib/reelEngine.ts`; the sequencing is the pure `src/lib/reelPlayer.ts`). Players
    find "My highlights" on their team page (`/teams/:id/highlights`).
  - Comments on the video page are pinned to the playhead (or the whole video), go to the
    team, its coaches or one person, and mention people from the video's roster with `@`.
    Comments with a time are markers on the strip below the player and on the workspace's
    overview timeline.
  - "Download as MP4" (coaches; on clips, reels and the rallies of a match) queues a
    watermarked export; the rules are shown once per device. `/downloads` lists them; the
    download is a plain link to `/api/exports/{id}/download`, which redirects to a 5-minute
    signed URL.
- **Stats** (M3b, volleyball; `src/lib/stats.ts`, `src/components/StatsTable.tsx`): the server
  computes the box score (`GET /api/videos/{id}/stats`, `/api/teams/{id}/stats`). Each number's
  moments are the tags matching its row's `filter`, its column's `codes` and `grades` and the
  set; `statTags` mirrors the server's counting. On the video page (`VideoStats.tsx`, below the
  player and the lists) a number plays its moments one after another in the player, and team
  numbers play their rallies; on the team page (`SeasonStats.tsx`, loaded on demand) a number
  opens the reel builder with that query (`?codes=A&grades=%23,none&side=them`; `none` is an
  ungraded tag). The name column is sticky, the table scrolls sideways inside its card on
  phones, percentages are de-CH (`33.3%`), "–" means no attempts, and the CSV is a plain link
  to `…/stats.csv?lang=en|de`.
- Text is English first, in sentence case. German strings are in `src/i18n/de.ts` (Swiss
  spelling, informal "du"); the type of `de` makes both files keep the same keys. The strings
  of the pages loaded on demand (video page and tagging: `video.*.ts`; clips, reels,
  comments, downloads: `library.*.ts`) are registered by `src/i18n/pages.ts` when one of those
  modules loads, so the main bundle doesn't carry them; a module using them imports it.

## Dependencies

Versions are pinned exactly (`.npmrc` has `save-exact`). When upgrading, prefer releases that
have been public for at least a week. TypeScript stays on 6.0 until typescript-eslint supports
a newer major.
