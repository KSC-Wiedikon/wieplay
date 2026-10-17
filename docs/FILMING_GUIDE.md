# Filming guide

For coaches and team managers of the teams that use WiePlay. German version:
[FILMING_GUIDE.de.md](FILMING_GUIDE.de.md). Technical background: `docs/PLAN.md` §5.3, §5.4,
§7.1 and §7.2.

## 1. Before you set up the camera

- **Matches only.** WiePlay takes matches: the team's games in Wiedisync, and other matches
  (friendlies, tournaments, cup games) with their date and a title. Trainings are not filmed
  for WiePlay for now.
- **Check WiePlay.** Open your team in WiePlay. Under *Filming*, every game of the next 21 days
  shows **Filming allowed** or **Filming blocked** with the reason. **Blocked means no
  camera.** WiePlay also refuses the upload afterwards. Other matches are not listed there; the
  same rules apply, and WiePlay checks them when you upload the video with its date.
- **Everyone is an adult.** WiePlay only takes footage when every person on the team's
  roster is 18 or older on that day and has a birthdate in Wiedisync. This is checked
  automatically; you cannot override it.
- **The team's filming rule.** An admin can switch off filming for a team. WiePlay then shows
  *Matches are not filmed for this team.*
- **No consent form.** Players are not asked one by one. Swiss Volley's rules allow anyone to
  record a match (Volleyballreglement 2026/27, Art. 86), and the club films its matches for
  WiePlay on that basis. Players learn about it from WiePlay's privacy page. Anyone who does not
  want to be in a video tells a coach or a WiePlay admin, who deletes the video or removes the
  person from its tags.
- **Opponents.** In adult leagues the club films without asking each opponent. Opponents
  appear by shirt number only. If an opponent asks, the footage is trimmed or deleted.

## 2. Where the camera goes

### Volleyball: behind the court

- Tripod **centred behind the end line**, **2.5 to 3 m high** (at least 1.5 m).
- The **whole court plus about 2 m around it**, with **all four corners of the far half**
  visible.
- **Fixed zoom.** Do not move, pan or zoom the camera during the match.
- **No wide-angle or fisheye mode.**
- Scoreboard in the picture if that is possible without losing court.
- **Away matches**: the home team provides a camera spot behind the court, centred, about
  3 m high (Volleyballreglement, Art. 86). Ask for it before the warm-up.

### Basketball (Herren 1): side of the court

- On the **long side at the half-court line**, as high as the hall allows (gallery or top
  row of the stands), **both baskets in the picture**.
- Fixed, no panning. If the hall is too narrow, a mild wide-angle setting is fine; never
  fisheye.

## 3. Camera settings

| Setting | Use | Why |
|---|---|---|
| Resolution, frame rate | **1080p at 50 fps** at least; **4K at 50 fps** if the camera can | Detail for the far court; 50 fps for fast actions |
| Not 60 fps | **50 fps** | Hall lights flicker at 50 Hz; 60 fps can show it |
| Shutter | **1/500 s or faster**; slower only if the picture shows flicker bands | Motion blur makes the ball invisible |
| Exposure, focus, white balance | **Locked (manual)** | Auto modes pump during play |
| Audio | **On** | WiePlay uses the whistles to find the rallies |

WiePlay reads all of this from the file. A video below the standard (say 25 fps or 720p)
still plays and can be tagged, but the automatic analysis skips it.

## 4. During the match

- Start at the **warm-up** and film **continuously**. Do not pause between sets.
- Cameras split long recordings into several files. That is fine: upload all of them as
  one video and WiePlay joins them.
- Storage: a 2-hour match at 1080p50 is about **25 to 45 GB**. Bring a charged battery or
  mains power and an empty card.
- Point the camera at the court, not at the stands.

## 5. Getting the video into WiePlay

On your team's page: *Upload video* → pick the match, or *Other match (friendly, tournament,
cup)* with its date and a title → choose where the video comes from:

- **From this device.** Choose all files of the recording → *Start upload*. Keep the page
  open until it says done. An interrupted upload continues when you select the same files
  again on the video page.
- **From the NAS inbox** (for people with access to the club NAS). Copy the camera files
  into your team's folder in the `inbox` folder of the `wieplay` share (for example `inbox/vb-H3`). After a few
  minutes they appear under *From the NAS inbox*. Pick them → *Add video*. They are not
  uploaded again.
- **From a link.** Is the match already on YouTube or Twitch, on the club's channel or a
  member's own channel? Paste the link of the video → *Import video*, and WiePlay fetches it
  itself. Only channels on the club's list work (an admin adds them under *Admin → Import
  channels*). Put videos on YouTube as *Unlisted*: anyone with the YouTube link can watch
  them there. Twitch deletes past broadcasts after 7 to 60 days, so import them soon after
  the match. The camera's original files are better quality, if you have them.

WiePlay then processes the video on the club's own server. It plays as soon as the first
quality (540p) is ready; full quality (1080p) follows. For a 2-hour match that takes about
half an hour in total (measured: 540p in 9 minutes, 1080p in 23 minutes). Keep the original
files until the video shows as *Ready*.

**Volleyball: rallies found automatically.** After that, WiePlay looks for the rallies itself
(from the ball, the movement on court and the whistles) and suggests them on the tagging page
under *Review suggestions*. Accept, correct or reject each one, then mark the rallies
reviewed: WiePlay learns from every game a coach has checked.

## 6. Rules that always apply

- WiePlay videos are for the team. **Never post them** or share screen recordings outside
  WiePlay.
- Delete requests (from a player, an opponent, anyone filmed): tell a coach or a WiePlay
  admin; the footage is trimmed or deleted, or the person is removed from the tags.
- If someone under 18 ended up in the picture anyway (a ball kid, a visitor on the court):
  tell a WiePlay admin before uploading.

## Checklist

- [ ] It is a match, and WiePlay says *Filming allowed* for it (other matches: checked at upload)
- [ ] Camera behind the end line (volleyball) or at the half-court line (basketball), high, fixed
- [ ] Whole court and the far corners in the picture, fixed zoom, no fisheye
- [ ] 1080p or 4K at **50 fps**, shutter 1/500 s, exposure/focus/white balance locked, audio on
- [ ] Battery and card checked; filming from the warm-up, no pauses
- [ ] Uploaded in WiePlay (or copied to the NAS inbox, or imported from a link); originals kept until *Ready*
