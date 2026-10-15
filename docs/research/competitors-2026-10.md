# Competitor research: Balltime, Hudl and others

Checked 15.10.2026 from vendor help centres, manuals, changelogs and pricing pages. Used for
`docs/PLAN.md` §12.

Legend: **[docs]** = vendor help centre, manual or changelog. **[mkt]** = only on a marketing
page, not confirmed in docs. **[3rd]** = third-party or competitor source. Most vendor pages
are undated; dates are given where a page shows one. Pages were read through a summariser, and
some prices and tier assignments are inferred (noted inline). Re-check before relying on any
number.

## Key findings

- **Balltime is now part of Hudl** (acquisition completed 06.02.2025,
  [Hudl blog](https://www.hudl.com/blog/hudl-expands-volleyball-focus-through-game-changing-acquisition-of-balltime)).
  balltime.com redirects to
  [hudl.com/products/assist/volleyball/ai](https://www.hudl.com/products/assist/volleyball/ai);
  the product is "Hudl Assist powered by Balltime AI". **Volleyball only** (indoor, beach,
  grass); no Balltime for basketball.
- **Their recording spec is our setup**: camera centred on the end line, tripod 8–10 ft high
  (minimum 5 ft), 1080p at 30 or 60 fps, no wide-angle or fisheye lens, all four corners of the
  far court visible ([Hudl record guide](https://www.hudl.com/support/hudl-volleyball/guides/record-and-upload),
  [Balltime FAQ](https://academy.balltime.com/getting-started/faqs)).
- **Generic camera platforms have no volleyball AI events.** Veo, Spiideo and Trace all stop
  short of it. Pixellot claims volleyball highlights, but only on a marketing page.
- **Scoreboard OCR was found in no product.** Auto-scoring exists only implicitly: Balltime's
  generated score is editable; VS Next claims to "recognise scores" [mkt].
- **No product documents timeouts or set breaks as separate segments.** Balltime's "downtime
  removal" covers all non-play.

## 1. Balltime, now Hudl Assist powered by Balltime AI

Team subscriptions move to Hudl at renewal; individual ones stay on app.balltime.com until
"2026 or later" ([hudl.com/hudl-balltime](https://www.hudl.com/hudl-balltime)).

- **Playback [docs]**:
  - Downtime is removed automatically; Balltime cites average downtime of about 67 % indoors
    and 65 % on the beach.
  - Every rally becomes a clip in a list, filterable by player, action, outcome, rotation and set.
  - Several video files can be merged into one match.
  - Sources: [what is volleyball AI](https://academy.balltime.com/getting-started/what-is-volleyball-ai),
    [FAQ](https://academy.balltime.com/getting-started/faqs).
- **AI pipeline [docs]**, in this order: downtime removal → actions (serve, pass, set, attack,
  block, dig) → team assignment → individual players → ball tracking (trajectories, speeds) →
  ball and player court positions (dig/attack tendencies, rotation charts). The pricing page
  adds vertical jump and serve, attack and set speeds.
- **Claims [mkt]** ([Hudl blog, 28.07.2025](https://www.hudl.com/blog/ai-volleyball)):
  - Stats arrive in 1–2 hours.
  - Pass rating is computed from location, airtime and setter position.
  - Heat maps of opponent attack and target zones; "AI Insights" surface trends; batch processing.
- **Corrections [docs]**: a pencil tool edits rallies, score, actions, outcomes and athlete tags.
- **Highlights**:
  - Highlight Builder across matches; vertical reel export with an email when ready; export to
    YouTube only.
  - The Recruiting tier adds AI recruiting videos, position templates, an intro-slide builder
    and a "player spot shadow".
- **Pricing (USD)** ([hudl.com/pricing/balltime](https://www.hudl.com/pricing/balltime)):
  - Player $20/month, 15 h of video per month.
  - Recruiting $25/month or $299/year, 200 h per year.
  - Team: quote only, via Hudl packages; 14-day trial.

## 2. Hudl

### 2a. Hudl volleyball platform (AI breakdowns) [docs]

Sources: [navigate video](https://www.hudl.com/support/hudl-volleyball/guides/navigate-video),
[filtering](https://www.hudl.com/support/hudl-volleyball/guides/filtering),
[completing data](https://www.hudl.com/support/hudl-volleyball/guides/completing-data),
[reports](https://www.hudl.com/support/hudl-volleyball/guides/reports).

- **Navigation**:
  - Sidebar views:
    - List: contacts grouped by rally.
    - Grid: editable table of result, quality, score, zones, speed, height, phase and video time.
    - Timeline: per-team rows for Serve, Receive, Set, Attack, Dig, Block, Cover, Free Ball and
      Violation.
  - Clicking a contact seeks the video there.
  - Search bar accepts "Attack" or "#3 Name"; filters can be saved.
  - Filter dimensions: team, athlete, drill, set, rotation, contact type plus result, phase
    (first ball or transition), from/to zone grid, violations, annotated clips, custom tags.
  - Speed control, frame stepping and keyboard shortcuts are not documented.
- **Annotation and sharing**:
  - Drawing: text, arrow, line, circle, block, freehand, laser pen.
  - Comments pinned to a timestamp and rally, with an audience (Everyone, Coaches, All Team or
    one user) and **@mentions**.
  - Clips, playlists, Highlight Builder, "Send to Team Highlights".
  - Roles: Viewer, Editor, Manager ([Explore guide](https://www.hudl.com/support/hudl-volleyball/guides/explore)).
- **AI overlays**: court, speed, contact height and net, plus serve, reception, set and dig
  trajectories.
- **Review tab (human in the loop)**, the most useful pattern for Nomal:
  - Fix set boundaries: drag a set's start, "+Add Set" at a timestamp, edit the starting score.
  - Enter each team's starting rotation per set (the Rotations report needs it).
  - **Court calibration**: drag 6 points (antenna tops left/right, far line left/right, middle
    line left/right).
  - Fill missing attack start/end points on a court diagram and mark Kill, Error or In Play;
    answer Yes/No for blocks.
  - **Unidentified-athlete queue**: shows a photo and contact count; assign a roster player, or
    a jersey number for opponents.
  - **Regrade**: receptions 0–3 and attacks 1–3 with number keys, U to undo, the list advances
    automatically.
- **Reports**:
  - Athlete and team stats, box score (every stat links to video).
  - Attack tendencies (lines, distribution, efficiency); Rotations (6 cards, attacks by pass
    rating); setter tendencies (3×3 heatmap); trends; 3D contact height; goals.
  - **CSV export**; print.
  - Assist lists side-out % and service-win % ([Assist volleyball](https://www.hudl.com/products/assist/volleyball)).
- **Availability**: AI for club teams now; college for practices; high school "coming in 2026".
- **Pricing, club volleyball** (USD per team per year, US programs, multi-team or club
  packages; [pricing](https://www.hudl.com/pricing/club/volleyball)):
  - **Core $500** (50 h): AI breakdowns, comments, playlists, box score, trajectories, ball
    speed, attack height, auto highlights, vertical reels, livestreaming.
  - **Plus $1,250** (100 h): adds analyst-supported breakdowns, AI performance summaries,
    rotation charts, tendencies and heatmaps, 3D, trends, goals, recruiting.
  - **Premier $1,600** (200 h): adds accelerated AI, scouting breakdowns and reports, custom
    pass ratings, setter analysis, defensive positioning, play calls.
  - Tier checkmarks did not render, so the tier assignment is inferred.

### 2b. Hudl Assist (human analysts)

Sources: [FAQ](https://www.hudl.com/products/assist/faq),
[basketball](https://www.hudl.com/products/assist/basketball).

- **Volleyball**: AI breakdowns need endline recording and are for club teams only. Fix errors
  yourself or ask an analyst; edits update reports. Each rostered player gets a playlist
  automatically.
- **Plans**: Game or Game+Scout, up to 3 games per 24 h, Standard or Express priority, current
  season only.
- **Basketball**: human-tagged, no AI mentioned; "4 hours per breakdown".
  - Stats: plus-minus, lineup efficiency, points per possession, inbound efficiency,
    deflections.
  - Reports: zone shot chart, opponent breakdown, trends, goals; every stat links to video.
  - Assist price not public (a competitor claims ~$900–1,500 per team [3rd]).
- **Club basketball pricing** (USD per team per year): Bronze $400 (50 h), Silver $1,000
  (100 h), Gold $1,600 (200 h); Assist and Focus are add-ons
  ([pricing](https://www.hudl.com/pricing/club/basketball)).

### 2c. Volleymetrics (college/pro)

- Analysts code every touch for both teams within 12 h.
- League exchanges share matches automatically; a worldwide match database supports scouting.
- **DVW download** if purchased ("our codes are compatible" with Data Volley).
- Demo-only pricing.
- Sources: [product page](https://www.hudl.com/en_gb/products/volleymetrics),
  [preseason blog](https://www.hudl.com/blog/the-essential-preseason-checklist-for-volleymetrics).

### 2d. Sportscode, Studio, Focus

- **Sportscode**: macOS offline coding with scripting and live capture (Hudl Coda on iPad);
  XML edit lists and Final Cut XML export
  ([manual](https://www.hudl.com/support/manuals/sportscode/distribute/exporting-data-files)).
- **Studio**: telestration assisted by computer vision [mkt].
- **Focus cameras** (Indoor, Flex, Point): auto-tracking and panoramic hardware with automatic
  start and upload ([Focus](https://www.hudl.com/products/focus)). No public prices.

## 3. Data Project

- **DataVolley 4** ([product page](https://www.dataproject.com/Products/EU/en/Volleyball/DataVolley4)):
  Windows only, USB dongle.
  - Live keyboard code entry with automatic linked skills (serve → reception, attack → block).
  - Video import and sync; rotation search returns rallies by rotation, side-out/break-point,
    won/lost; video export with text and drawings.
  - Reports: player, skill, rotation, zone/direction, combinations, setter calls,
    point-by-point.
  - Pro adds live capture/streaming, montages, custom formulas, a bench Web Client and a Player
    App.
  - Format: **.dvw**.
  - **Price**: Pro €799/year, LITE €299/year.
- **Click&Scout** ([product page](https://www.dataproject.com/Products/EU/en/Volleyball/ClickAndScout)):
  touch-court scouting (Windows, Android, iPad), DataVolley-compatible files; video sync needs
  "Data Video Essential"; €49/year.
- **VBStats** is by Perana Sports, not Data Project ([App Store](https://apps.apple.com/app/id575141935)):
  - iPad, $29.99 one-off; video sync, rotation analysis, linked iPads, export to Hudl.
  - **DVMate** (also Perana) syncs DataVolley files and VERT jump data to video.

## 4. VolleyStation

- Line-up ([volleystation.com](https://volleystation.com/)): VS Score (e-scoresheet), VS Next
  (computer vision), VS Online, league suite, remote scouting.
- **VS Next [mkt]** ([next.volleystation.com](https://next.volleystation.com/)):
  - Single camera or phone.
  - Automatic player identification and skill recognition; claims to recognise "movements,
    scores, and patterns".
  - Workload metrics without wearables.
  - Automatic per-player reels, comments, social sharing.
  - Token pricing, first match free.
- **VolleyStation Pro**: automatic play timestamps with a sync-fix screen, montage builder,
  **Sportscode XML export** ([blog](https://blog.volleystation.com/integration-with-hudl-sportscode/)),
  XLSX export. DVW support not confirmed.

## 5. SoloStats and open source

- **SoloStats** (Rotate 123 LLC):
  - Live stat apps with automatic score and rotation tracking from entered stats, plus side-out
    and point-scoring % ([App Store](https://apps.apple.com/app/id499803638)).
  - SoloStats VIDEO syncs stats to video [mkt].
  - Stat capture is free; in-app purchases are $5–13 per year. No AI.
- **ovscout2** (open source, R/Shiny; [manual](https://openvolley.r-universe.dev/ovscout2/doc/ovscout2-user-manual.html)):
  - Fixed camera behind the end line, **4-corner court calibration**.
  - Click the video at each contact to record its floor location; video pauses for input after
    serve, set and attack.
  - Typed-code mode with remappable shortcuts; automatic pass grading.
  - Writes **.dvw** (also after every rally for the bench).
  - The closest open prior art to Nomal's tier 1.

## 6. Camera and AI platforms

- **Veo**: basketball and volleyball get follow-cam, a panoramic player and drawing only; AI
  events and clips are soccer, rugby and lacrosse only
  ([support, 24.08.2026](https://support.veo.com/hc/en-us/articles/30159089390865)).
- **Pixellot**:
  - Air NXT dual-stream camera ([SVG, 2024](https://www.sportsvideo.org/2024/09/27/introducing-pixellot-air-nxt-the-next-generation-of-the-multi-sports-portable-ai-camera/)).
  - Volleyball "personal highlights, 100 % automatically" [mkt] ([page](https://pixellot.tv/sports/volleyball/)).
  - VidSwap human tagging in 4–8 h with stats recalculated on edit
    ([docs](https://docs.pixellot.tv/portal/en/kb/vidswap/uploading-filming-videos/stats)).
  - **Condensed basketball games and per-player reels** within about an hour via SportsEngine
    Play ([15.04.2026](https://youthsportsbusinessreport.com/?p=19789)).
  - Players are recognised by height, hair, jersey colour and number, without face recognition
    ([interview, 30.08.2026](https://www.pixellot.tv/?p=256528)).
- **Trace**: AI for soccer and basketball, not volleyball; PlayerFocus per-player playlists
  ([help](https://support.traceup.com/content/how-to-record-more-sports)).
- **Spiideo**:
  - AI Highlights (May 2026) cover soccer, ice hockey, basketball and handball, not volleyball
    ([support, 18.06.2026](https://support.spiideo.com/en/articles/15070462-ai-highlights-and-stories-in-spiideo-play)).
  - Perform: tagging, drawings, comments, multi-angle [mkt].
- **PlaySight**: AI highlights for racquet sports; nothing specific found for basketball or
  volleyball [3rd].

## 7. Basketball tools

- **SportsVisio** ([pricing](https://www.sportsvisio.com/pricing)), basketball and volleyball:
  - Box score, clips per stat and player, and per-player reels within 24 h, filmed on any device.
  - Coach Mode adds an AI assistant coach, shot charts and game flow.
  - **Basketball**: $199/month unlimited for one team; Coach Mode $750 for a season of 25 games.
  - **Volleyball**: $249/month or a $900 season pack (22 games).
  - No correction workflow documented.
- **Synergy (Sportradar)** ([page](https://sportradar.com/synergy-basketball-for-high-school/)):
  500+ breakdown categories in under 24 h, editors, shared libraries, Shot Quality insights;
  12-month contract, no public price.
- **Catapult Pro Video** ([blog, 06.11.2023](https://www.catapult.com/blog/pro-video-basketball-innovation-nba-ncaa)):
  multi-angle capture, live bench tagging, annotations, wearable data. May be outdated.

## Master feature list

| Feature | Who has it |
|---|---|
| Auto rally detection, downtime removal, condensed playback | Balltime/Hudl (~67 % removed), Pixellot (basketball 2026; volleyball [mkt]), VS Next (implied). Rally-only playback from manual codes: DataVolley, VolleyStation Pro, ovscout2 |
| Timeouts and set breaks as separate segments | Not documented anywhere |
| Editing set boundaries and starting score | Hudl Review tab |
| Auto scoring from video | Balltime (editable), VS Next [mkt], SportsVisio. From manual codes: DataVolley, SoloStats, ovscout2, VBStats |
| Scoreboard OCR | Not found |
| Automatic player identification | Hudl/Balltime (with review queue), VS Next [mkt], Pixellot [mkt], Trace, SportsVisio, Spiideo |
| Ball tracking, trajectories, speed | Hudl/Balltime |
| Contact or jump height | Hudl/Balltime, Pixellot, VBStats (VERT wearable) |
| Court calibration | Hudl (6 points), ovscout2 (4 corners) |
| Review and correction queue for AI output | Hudl (best example), Balltime edit tool, VolleyStation sync screen |
| Filters, saved filters, search | Hudl, Balltime, DataVolley, VolleyStation, Spiideo |
| Click a stat to open its video | Hudl, DataVolley, SportsVisio, Synergy |
| Heatmaps, zone and shot charts | Hudl, DataVolley, Pixellot, SportsVisio, Synergy |
| Serve and attack charts or lines | Hudl, DataVolley, ovscout2 |
| Rotation analysis | Hudl, DataVolley, VBStats, SoloStats |
| Side-out and break-point % | Hudl Assist, DataVolley, SoloStats |
| Pass rating 0–3 (incl. automatic) | Hudl, ovscout2 |
| Setter distribution | Hudl, DataVolley |
| Season trends and goals | Hudl, Synergy |
| AI summaries / "assistant coach" | Hudl Plus, SportsVisio Coach Mode |
| Automatic per-player highlight reels | Balltime/Hudl, VS Next, SportsVisio, Pixellot, Trace |
| Vertical social reels, recruiting | Balltime/Hudl, VS Next |
| Clips, playlists, montages | Most products |
| Comments with @mentions and audience | Hudl; comments only: Volleymetrics, VS Next, Spiideo |
| Telestration and drawing | Hudl, Veo, Spiideo, Pixellot, Catapult, DataVolley (exported video) |
| Data overlays (court, speed, net) | Hudl |
| Slow motion, frame stepping, hotkeys | Not documented in the sources read |
| Multi-angle, panoramic, auto-tracking cameras | Hudl Focus, Veo, Pixellot, Trace, Spiideo, Catapult |
| Livestreaming | Hudl, Pixellot, Spiideo, DataVolley Pro |
| Opponent scouting libraries, league exchange | Hudl, Volleymetrics, DataVolley, Synergy |
| Live tagging, live data to the bench | DataVolley, Click&Scout, VBStats, SoloStats, VolleyStation, Sportscode, Catapult, Synergy |
| Practice and drill breakdowns | Balltime/Hudl, VBStats, SoloStats |
| Workload metrics from video | VS Next [mkt], Pixellot |
| DVW export or import | DataVolley, Click&Scout, Volleymetrics (paid), ovscout2, DVMate (import) |
| CSV / XLSX export | Hudl (CSV), VolleyStation (XLSX) |
| Merge several files into one match | Balltime/Hudl |
