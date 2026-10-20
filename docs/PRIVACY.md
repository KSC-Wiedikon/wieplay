# WiePlay: data protection

**Draft, 15.10.2026, updated 17.10.2026** (guest clubs, §11), **18.10.2026** (a guest
club's next season, §8 and §11) **and 19.10.2026** (official scoresheets, their photos and their reading on club hardware, §6),
for review by the club's privacy contact before
WiePlay is used beyond the pilot teams. Not legal advice. It describes what WiePlay does today and
what the plan adds later; design background in `docs/PLAN.md` §5 and §10.1.

## 1. Who is responsible

KSC Wiedikon (the club) is responsible for the processing. Contact: the club's privacy
contact (to be named on the privacy page). WiePlay is built and run by club volunteers. A guest
club (§11) is responsible for its own footage and for informing its people; KSC Wiedikon
hosts it.

## 2. What WiePlay is for

Video analysis for the club's own teams, and for the teams of guest clubs the club lets in
(§11): coaches and players watch, tag and discuss footage of their **matches** to improve.
Trainings are not filmed for WiePlay for now. No publication, no commercial use, no scouting of
other clubs, no use outside the team.

Videos come in three ways, all for a match (a game from Wiedisync, or a friendly, tournament
or cup game with its date and a title):

- **Browser upload** of the camera files by a coach or team manager.
- **NAS inbox**: camera files copied onto the club NAS.
- **Import from a link**: a video the club or a member already put on YouTube or Twitch, on
  a channel from the club's list; WiePlay's server fetches a copy (`docs/PLAN.md` §7.2.2).

All three go through the same checks (§5) and the same processing.

## 3. Whose data, which data

| People | Data in WiePlay | Source |
|---|---|---|
| Players, coaches, team managers of teams that use WiePlay (adults only) | Name, shirt number, positions, which team and season; video (picture and sound); tags of their actions (skill, grade, zone, time); comments, clips and reels | Wiedisync (names, numbers, rosters), WiePlay |
| Opponents | Video; shirt numbers in tags. No names | Footage |
| Other people in the hall (referees, spectators) | Incidentally in the video. The camera is framed on the court | Footage |
| Everyone on court | Analysis signals derived from the video: ball positions, movement, sound levels and whistles (§6). No pictures, no sound, no names | WiePlay |
| Everyone who logs in | Session (8 hours), audit log entries (logins, videos watched, uploads, imports, deletions, exports, permission changes) | WiePlay |
| People of a guest club (§11) | As for the club's own players and coaches above; the email they log in with and their password (as a scrypt hash) | The guest club's coaches (names, numbers), the person (email, password) |
| Both teams, the referees and the scorer of a filmed league match (§6) | The official scoresheet as typed: shirt numbers and scores only. Optionally a photo (or a PDF scan, drawn into a picture on the device) of the paper sheet: names and shirt numbers of both teams, the officials' names and signatures as written on it; **the dates of birth and licence numbers (team lists, officials' block) are blacked out on the coach's device before the upload** | The team's copy of the sheet, typed or photographed by a coach |

**Not in WiePlay**: email (except a guest club's logins, §11), phone, address, birthdate. Whether everyone on a roster is 18 or
older is answered by Wiedisync as yes or no with counts; birthdates never leave Wiedisync. The
dates of birth on a paper scoresheet, and the licence numbers next to the players' and the
officials' names (often dates of birth too), are blacked out before its photo leaves the coach's
device (§6).

## 4. Why it is allowed

- **Filming matches.** Swiss Volley's rules allow third parties to record a match
  (Volleyballreglement 2026/27, Art. 86 "video recording"; the home team provides the
  opponent's camera spot behind the court, centred, about 3 m high). The club films its own
  matches for WiePlay on that basis (decided 16.10.2026) and keeps the footage inside the team.
  It relies on its interest in analysing its own matches (revDSG Art. 31) together with the
  safeguards below, so players are **not asked one by one**; the opt-in per player of the
  first draft is gone.
- **Information instead of consent.** Members are told what is filmed, who sees it, how
  long it is kept and who processes it: in a WiePlay section of the privacy policy on kscw.ch
  and on WiePlay's own privacy page. Processing is described as "on club hardware, or at a
  provider in Switzerland or the EU named on the privacy page", so a later move to a GPU
  provider needs an updated privacy page and a notice to members. A member who objects asks a
  coach or admin, who deletes the video or removes the member from its tags.
- **Opponents**: the club's position (15.10.2026) is that adult-league matches may be
  filmed for internal analysis without asking each opponent. Opponents appear by number
  only, the footage stays within the team, and it is trimmed or deleted on request.
- **Imported videos**: only from the club's channel and members' own channels on a list a
  coach or admin confirms. Their owners already published them; WiePlay keeps a copy for the
  team and applies the same rules as to an upload.
- **Incidental people**: framing on the court keeps them out as far as possible.
- **Team filming rule**: an admin can switch off filming for a team (for example when its
  opponents may be under 18); WiePlay then refuses its videos.

## 5. Nothing of anyone under 18

Enforced in code, not only stated:

- Before anything is stored, WiePlay asks Wiedisync whether every person on the team's
  roster is 18 or older **on the recording day** (no birthdate counts as a fail). This runs
  for every upload, inbox file and link import, again when an upload finishes, and on the
  camera's own recording date too.
- Refused means nothing is kept: no video, no upload parts, no imported file.
- The team page shows *filming allowed / blocked* for the games of the next 21 days, so
  nobody films a blocked match.
- Teams with minors on the roster are switched off in WiePlay (DU23-1 since 15.10.2026),
  which also keeps their names out of WiePlay.
- A guest club's team (§11) is not in Wiedisync: instead a coach of the team confirms once a
  season, before the season's first upload, that everyone filmed (players, coaches and
  opponents) is 18 or older (an adult league). Without that confirmation WiePlay refuses the
  team's videos.

## 6. Automatic analysis

To skip the dead time between rallies and to save coaches tagging time, WiePlay looks for the
rallies in each volleyball match by itself (`docs/PLAN.md` §10.1).

| | |
|---|---|
| Purpose | Propose the rallies of a match, so coaches can watch only the rallies and tag faster |
| What runs | On the club's Lenovo server at home, once a video is ready and meets the filming standard: WiePlay decodes the original and records, per frame, where the ball is, how much moves on court and how loud it is, including the referee's whistles |
| Data kept | These **signals**: one compressed file of a few MB per match. No pictures, no sound, no faces, no names. They cover the whole court, so both teams |
| Result | Rally proposals. Nothing counts as fact until a coach accepts, corrects or rejects it |
| Learning | Models train only on the club's own games whose rallies a coach has reviewed, on club hardware. A model holds learned weights, no pictures and no names; the games it used are listed with it |
| Where | Signals and models on the club NAS, next to the originals |
| Who sees it | The proposals: the team, like the video; its coaches review them. The signals themselves stay on the NAS and are not shown |
| Retention | Signals as long as the video: deleted with it. Models until they are retired |

What WiePlay never does: no face recognition, no face embeddings, no recognising a person
across videos by their looks. A player is linked to an action only through the shirt number in
the team's roster (`docs/PLAN.md` §5.7); across a team's games only its shirt numbers and
lineups carry over (the team's memory, below).

**Players in a match** (`docs/PLAN.md` §10.5, since 17.10.2026): on the same server, WiePlay
finds the people on the court during the rallies, follows them from frame to frame and
groups them into "a person in this video". It keeps their boxes per frame and a few
**pictures of each person** (crops, mostly of the back with the shirt number) so a coach can
say once which roster number each card is; WiePlay then links the actions to the players.

| | |
|---|---|
| Purpose | Tag who made each action without a coach typing every number |
| What is used | Position on court, the colour of the kit and the number on the shirt. **No faces, no face embeddings, no person re-identification model, no looks across videos**: each video's cards start from zero, and a card is anonymous until a coach gives it a roster number (the number reader only suggests one; since 20.10.2026 a number the team's earlier games named is offered as recognised, for a coach to accept) |
| Data kept | Boxes per frame (numbers), the crops (JPEG), the clusters and the coach's numbers |
| Where | On the club NAS next to the signals, and the crops in the video's own storage |
| Who sees it | The crops: those who may watch the video (they show what the video shows). Naming, merging and splitting: the team's coaches |
| Retention | As the video: deleted with it |

**The team's memory** (`docs/PLAN.md` §10.5b, since 20.10.2026): per season's team, WiePlay
keeps which of **our shirt numbers** its games named (in a typed sheet's lineups and
substitutions, a coach's starting six, a card or a tag a coach named), how often, the member
wearing each number (the roster's link, or a coach's fix on the team page), and the team's
usual service orders and starting rotations, as numbers. A new game of the team uses them: a
card whose shirt reads a number the team plays is offered as recognised (a coach accepts it),
and where the sheet is missing our likely server per rally comes from the usual rotation.

| | |
|---|---|
| Purpose | A team's second game needs fewer answers than its first; serves get their player without a sheet |
| What is used | Shirt numbers, the roster, the sheets' and the coaches' facts. **No faces, no face or body embeddings, no picture, no colour or look of a person** is stored or compared across videos. **Nothing about the opponents** (their numbers stay in their video). A guest team's memory is its own |
| Data kept | Per number: the member, games and sets counted, the last game's date; per team: the orders and starting rotations (numbers), the models' scores |
| Who sees it | The team page: whoever sees the team. Fixing a number: its coaches and helpers |
| Retention | Learnt anew from the team's games whenever they change: a deleted video drops out of it |

**The club scoreboard's log** (`docs/PLAN.md` §10.10, since 18.10.2026): for a video of a
Wiedisync game, WiePlay asks Wiedisync for what the club's LED scoreboard showed during that
game, point by point (each point's time on the board's clock, the score, the sets, timeouts,
who served), and lines it up with the video's rallies to check the score line. **Team names
and scores only**: the log names no person, and Wiedisync sends nothing about who operated the
board. Kept with the video's analysis (deleted with the video); seen like the score line.

**Official scoresheets** (`docs/PLAN.md` §10.3a, since 19.10.2026): every league match has a
paper scoresheet, and each team gets a copy. A coach (or anyone who tags the match) may type its
numbers into WiePlay, so WiePlay knows the winner of every point. Typed are **shirt numbers and
scores only**: no names, no dates of birth, no licence numbers.

The team's coaches may also add a **photo of the paper sheet**, to type it in next to it (the
owner's decision of 18.10.2026):

| | |
|---|---|
| Purpose | Type the sheet next to a picture of it, or let WiePlay read its numbers on club hardware and check them (since 19.10.2026) |
| Dates of birth | Since 2022 the sheet lists every player's date of birth in the licence column, and the officials' licence column often holds dates of birth too. **They are never stored**: before the upload the coach covers the team lists and the officials' block with black boxes on the phone or computer (two boxes are placed there to start with; a step WiePlay requires); the picture is redrawn with the black burned in and only that picture is uploaded. The original photo never leaves the device; a PDF scan is drawn into a picture there and only the blacked-out picture is uploaded |
| What is stored | That picture as a JPEG: the score grids, and the names and shirt numbers of both teams, officials' names and signatures as on the sheet (the coach may black out more). **No camera data**: no place, no time, no device (WiePlay refuses a picture that still carries them and keeps only its pixels) |
| Where | The WiePlay bucket at Hetzner in Germany only (`sheets/`); not in any other service, not on the NAS |
| Who sees it | The team's coaches and team managers, sport admins and the club's admins; a guest team's own coaches. **Not players, not helpers** (they see the typed numbers). Through a link that works for 5 minutes, and **every view is logged** |
| Reading | On club hardware only (since 19.10.2026): a job on the club's Lenovo server (CPU) fetches the photo from the bucket through a link signed for that job, reads it in memory (a small digit reader trained by the club on made-up sheets; no outside service, no hosted model, no copy written to disk) and returns the numbers it read and where each cell is on the photo. The result is a draft a coach checks and confirms; confirmed cells are kept as labels (the cell's place on the photo and its value, no copy of the picture) to improve the reader, and go with the photo |
| Retention | As long as the video, deleted with it; a coach can remove a photo at any time (it is deleted from storage at once), and removing the typed sheet removes its photos |

Later stages (players and ball tracking on a GPU) run on club hardware first. A move to a
GPU provider in Switzerland or the EU needs a DPA and an updated privacy page; the provider
would get only short, temporary, court-cropped copies of the rallies, with no names or IDs.

## 7. Where the data is, who processes it

| Where / who | What | Place | Note |
|---|---|---|---|
| Hetzner (server and object storage), the club's own account since 16.10.2026 | Database; playable video (renditions), thumbnails, exports, upload staging, scoresheet photos in the buckets | Germany (Nuremberg, nbg1) | Processor; DPA with Hetzner needed |
| NAS of the club's IT admin | Original camera files and imported files, the camera inbox, analysis signals and models | Switzerland, private home | Club-controlled hardware at a volunteer's home; access for the NAS administrators only; not copied offsite |
| Cloudflare | Web pages and API answers pass through; **no video bytes** (those come straight from object storage) | Global network | Already the club's provider for Wiedisync |
| Club hardware (Lenovo server; later a GPU laptop) | Converting videos, imports, automatic analysis, reading scoresheet photos (in memory, nothing kept there) | Switzerland, same home | No third party |
| YouTube (Google), Twitch | Source of imported videos only: WiePlay's server fetches the video; browsers never talk to them, and WiePlay plays its own copy | Google, Twitch | Only for videos from channels on the club's list |
| Later: a Swiss or EU GPU provider | Short, temporary analysis copies of rallies, no names or IDs | Switzerland or EU | Only with a DPA and an updated privacy page (PLAN §8.6) |

Database backups go into the club's existing encrypted backup (Google Drive, Cloudflare
R2, the NAS; encrypted before leaving the server).

## 8. How long

| Data | Kept |
|---|---|
| Videos, thumbnails, originals | The recording season plus one season (a 2026/27 match until 31.05.2028), then deleted automatically |
| Analysis signals | As long as the video; deleted with it |
| Rally models | Until the model is retired |
| Upload staging | Until processed, at most 14 days |
| Exports (watermarked MP4) | 30 days |
| Typed scoresheets and their photos | As long as the video; a photo until a coach removes it, at the latest with the video |
| Tags, rallies, scores, roster snapshots, comments | As long as the video |
| Audit log | 12 months |
| Sessions, login records | 30 days |
| A guest club's login (email, password hash) | While its person is on the team's current season; disabled when they leave it or are not taken into the next season, deleted 30 days later |
| Invite links | Work 14 days; deleted 30 days after they expired |
| Members' names and numbers | While their team uses WiePlay; removed when no WiePlay team lists them. A guest club's earlier season keeps its roster for that season and the next one, as Wiedisync does for the club's own |

A nightly job enforces this and logs what it deleted.

## 9. Protection

- Login only through Wiedisync; sessions expire after 8 hours. Guest clubs (§11) log in with
  an email and a password set from an invite link; their sessions expire after 14 days, and 10
  wrong passwords in a row lock the login for 15 minutes.
- Who sees what follows the club's roles: players see their own teams and seasons, coaches
  their teams, sport admins their sport, admins everything (and that is logged).
- **No public links.** Every piece of video is fetched through WiePlay with the session; the
  storage link it hands out works for 5 minutes and covers 4 seconds of video.
- Exports are for coaches only and carry a burned-in watermark with the name and date.
- Storage keys exist only on the server; the video worker gets single-use signed links.
- Every change to tags and to a typed scoresheet is kept in a revision log; deletions are
  logged. Scoresheet photos are seen by the team's coaches and admins only, and every view is
  logged.
- Encrypted connections everywhere (HTTPS; an SSH tunnel between the club's server and the
  Hetzner server).

## 10. Rights

- **See your data**: members can ask for their audit log entries and tags.
- **Correct**: names and numbers come from Wiedisync and are corrected there; tags in WiePlay.
- **Delete or trim**: on request a coach or admin deletes the video or the part, or removes
  the person from its tags.
- **Object**: a member who does not want to be filmed or kept in a video tells a coach or
  admin. There is no consent to withdraw; the request is handled as above.

## 11. Guest clubs

Since 17.10.2026 KSC Wiedikon can let teams of other volleyball clubs (guest clubs) use WiePlay
for their own matches (`docs/PLAN.md` §4.5; how an admin sets one up:
`docs/runbooks/guest-club.md`).

- **The guest club is responsible for its footage**: it decides to film its matches, it
  informs its players and coaches (what is filmed, who sees it, this notice), and it handles
  their requests. It agrees to this in writing with KSC Wiedikon before its team is created,
  including whether its games may train WiePlay (below).
- **KSC Wiedikon hosts it** with the same processors and rules as its own footage (§7): the
  Hetzner server and storage in Germany, the club's hardware in Switzerland, no third party in
  the browser, no public links, the same retention (§8).
- **Adults only**: WiePlay has no birthdates of a guest club's people. Before the first upload
  of each season a coach of the guest team confirms: "Everyone we film — our players, coaches
  and our opponents — is 18 or older (adult league)." Without it, WiePlay refuses its videos.
- **Who sees it**: only the guest team's own people (players watch, tag and review; coaches
  also upload, download, delete and keep the roster) and KSC Wiedikon's admins (logged). KSC Wiedikon's members
  never see a guest team, and guest clubs never see KSC Wiedikon's teams, videos or people.
- **Logins by invite only**: an admin invites the team's first coach, the coaches invite their
  people. Each link works once, for 14 days; WiePlay keeps only a hash of it and sends no email.
  A person taken off the team loses access at once and their name leaves WiePlay (their tags
  keep the shirt number); their login is deleted 30 days later.
- **A new season**: the guest team's coaches start the next season themselves and pick who
  comes along. Those taken along keep their login and see both seasons. Those not taken along
  stay on the old season's roster (name and number, as in its videos' tags) but lose access at
  once: their login is disabled and deleted 30 days later, unless a coach takes them along
  later. An old season's roster is kept for that season and the next one, like the club's own
  rosters from Wiedisync, and then removed, as its videos are (§8).
- **Training**: the automatic analysis runs on a guest team's games like on the club's own
  (§6), but its reviewed games train WiePlay's shared models only if the guest club agreed to it.
  Without that agreement they never go into a training run.
- **Limits**: a guest team has a number of videos per season (30 unless agreed otherwise); each
  new season starts from zero.
- **Deletion on request**: on the guest club's request KSC Wiedikon deletes its videos (with
  their originals and analysis signals), its people and their logins; the club or a person can
  also ask for a single video or part of it to be deleted, or a person to be removed from its
  tags (§10).

## 12. Open points for the privacy contact

1. Sign or record the DPA with Hetzner (the club's own account since 16.10.2026; server and
   object storage).
2. Agree that originals, analysis signals and models may stay on the club admin's home NAS,
   or name another place.
3. Confirm the legal basis in §4: Art. 86 of the Volleyballreglement as the frame for
   filming without asking each player, and whether the club applies the same to basketball
   (Herren 1) and to friendlies and tournaments.
4. Add a WiePlay section to the privacy policy on kscw.ch and a short privacy page in WiePlay
   (what, why, who sees it, how long, processors including YouTube and Twitch for imports,
   the automatic analysis, contact, rights), and tell the members.
5. Confirm the club's position on filming opponents in adult leagues (§4).
6. Guest clubs (§11): agree the text of the written agreement with a guest club and check the
   short privacy page in WiePlay (`/privacy`, a draft written with this document) before the first
   guest club is invited.
