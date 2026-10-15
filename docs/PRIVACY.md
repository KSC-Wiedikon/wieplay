# Nomal: data protection

**Draft, 15.10.2026**, for review by the club's privacy contact before Nomal is used beyond
the pilot teams. Not legal advice. It describes what Nomal does today (M0–M2) and what the
plan adds later; design background in `docs/PLAN.md` §5.

## 1. Who is responsible

KSC Wiedikon (the club) is responsible for the processing. Contact: the club's privacy
contact (to be named on the privacy page). Nomal is built and run by club volunteers.

## 2. What Nomal is for

Video analysis for the club's own teams: coaches and players watch, tag and discuss
footage of their matches and trainings to improve. No publication, no commercial use, no
scouting of other clubs, no use outside the team.

## 3. Whose data, which data

| People | Data in Nomal | Source |
|---|---|---|
| Players, coaches, team managers of teams that use Nomal (adults only) | Name, shirt number, positions, which team and season; consent answers; video (picture and sound); tags of their actions (skill, grade, zone, time); later comments and clips | Wiedisync (names, numbers, rosters), Nomal |
| Opponents | Video; shirt numbers in tags. No names | Footage |
| Other people in the hall (referees, spectators) | Incidentally in the video. The camera is framed on the court | Footage |
| Everyone who logs in | Session (8 hours), audit log entries (logins, videos watched, uploads, deletions, consent changes) | Nomal |

**Not in Nomal**: email, phone, address, birthdate. Whether everyone on a roster is 18 or
older is answered by Wiedisync as yes or no with counts; birthdates never leave Wiedisync.

## 4. Why it is allowed

- **Players**: explicit opt-in per player, recorded with the notice version, in Nomal or
  on a paper form a coach records. Withdrawal at any time; a team without everyone's
  consent cannot be filmed.
- **Opponents**: the club's position (15.10.2026) is that adult-league matches may be
  filmed for internal analysis without asking each opponent. Opponents appear by number
  only, the footage stays within the team, and it is trimmed or deleted on request.
- **Incidental people**: framing on the court keeps them out as far as possible.

## 5. Nothing of anyone under 18

Enforced in code, not only stated:

- Before anything is stored, Nomal asks Wiedisync whether every person on the team's
  roster is 18 or older **on the recording day** (no birthdate counts as a fail). It asks
  again when the upload finishes, and checks the camera's own recording date too.
- Refused means nothing is kept: no video, no upload parts.
- The team page shows *filming allowed / blocked* for the coming three weeks, so nobody
  films a blocked session.
- Teams with minors on the roster are switched off in Nomal (DU23-1 since 15.10.2026),
  which also keeps their names out of Nomal.

## 6. Where the data is, who processes it

| Where / who | What | Place | Note |
|---|---|---|---|
| Hetzner (server and object storage) | Everything Nomal stores: database, playable video, thumbnails | Germany (Nuremberg) | Processor; DPA with Hetzner needed |
| NAS of the club's IT admin | Original camera files, the camera inbox | Switzerland, private home | Club-controlled hardware at a volunteer's home; access for the NAS administrators only; not copied offsite |
| Cloudflare | Web pages and API answers pass through; **no video bytes** (those come straight from object storage) | Global network | Already the club's provider for Wiedisync |
| Club hardware (Lenovo server, later a GPU laptop) | Converting videos; later automatic analysis | Switzerland, same home | No third party |
| Later: a Swiss or EU GPU provider | Short, temporary analysis copies of rallies, no names or IDs | Switzerland or EU | Only with a DPA and an updated privacy page (PLAN §8.6) |
| YouTube (Google), later | Only for videos the club or a member already published on their own channel, embedded | Google | Not used yet |

Database backups go into the club's existing encrypted backup (Google Drive, Cloudflare
R2, the NAS; encrypted before leaving the server).

## 7. How long

| Data | Kept |
|---|---|
| Videos, thumbnails, originals | The recording season plus one season (a 2026/27 match until 31.05.2028), then deleted automatically |
| Upload staging | Until processed, at most 14 days |
| Tags, rallies, scores, roster snapshots | As long as the video |
| Audit log | 12 months |
| Sessions, login records | 30 days |
| Members' names and numbers | While their team uses Nomal; removed when no Nomal team lists them |

A nightly job enforces this and logs what it deleted.

## 8. Protection

- Login only through Wiedisync; sessions expire after 8 hours.
- Who sees what follows the club's roles: players see their own teams and seasons, coaches
  their teams, sport admins their sport, admins everything (and that is logged).
- **No public links.** Every piece of video is fetched through Nomal with the session; the
  storage link it hands out works for 5 minutes and covers 4 seconds of video.
- Storage keys exist only on the server; the video worker gets single-use signed links.
- Every change to tags is kept in a revision log; deletions are logged.
- Encrypted connections everywhere (HTTPS, the tailnet between club machines).

## 9. Rights

- **See your data**: members can ask for their audit log entries and tags.
- **Correct**: names and numbers come from Wiedisync and are corrected there; tags in Nomal.
- **Delete or trim**: on request a coach or admin deletes the video or the part.
- **Withdraw consent**: in Nomal at any time. The team is then not filmed until resolved;
  later milestones also turn the person's tags into "#number" and delete their personal clips.

## 10. Open points for the privacy contact

1. Sign or record the DPA with Hetzner (server and object storage).
2. Agree that originals may stay on the club admin's home NAS, or name another place.
3. Turn the consent notice in Nomal from *draft* into the final text (version change makes
   everyone answer again).
4. Add a Nomal section to the privacy policy on kscw.ch and a short privacy page in Nomal.
5. Confirm the club's position on filming opponents in adult leagues (§4).
