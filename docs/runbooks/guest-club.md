# Runbook: a guest club

What it is: [PLAN §4.5](../PLAN.md#45-guest-clubs) and [PRIVACY §11](../PRIVACY.md#11-guest-clubs).
A team of another volleyball club uses WiePlay for its own matches, with logins of its own; it
sees only its team, and KSCW's members never see it. Everything here is done in the app as a
full admin (Admin → Teams → Guest clubs); nothing touches the database by hand.

## Before the first guest club on prod (once)

- The club board agreed that WiePlay hosts other clubs' footage.
- The club's privacy contact checked the privacy page (`/privacy`, a draft) and the agreement
  below (PRIVACY §12, point 6).

## Onboard a club

1. **The club's written OK**, kept with the club's records (an email from the club's board or
   the team's head coach is enough). It says:
   - the club films its own adult-league matches and is responsible for that footage;
   - it informs its players and coaches (what is filmed, who sees it, the privacy page);
   - everyone filmed is 18 or older (its coach confirms it again in WiePlay);
   - whether its reviewed games may **train WiePlay's shared models** (yes or no);
   - how many videos per season (30 unless agreed otherwise);
   - deletion on request (below).
2. **Create the team**: Admin → Teams → Guest clubs → *Create a guest team*. Club as it is
   called ("VBC Zürich"), team ("Herren 2"), league (optional), season (the current one by
   default), video limit, and the training box only if the club said yes. It is switched on.
3. **Invite the coach**: right after creating it (or *Invite a coach* in its row), give the
   coach's name. WiePlay shows a link `https://wieplay.kscw.ch/join/…` **once**: copy it and send it
   to the coach only (a direct message, not a group chat). It works once, for 14 days.
4. **The coach** opens the link, sets an email and a password and lands on the team page,
   where they:
   - confirm once that everyone filmed is 18 or older (no uploads before that);
   - add their players and other coaches under **People** (name, number, role) and send each
     one their own invite link (*Invite link*, shown once);
   - upload a match: a date and a title ("Friendly vs Volero"); there are no Wiedisync games,
     no NAS inbox and no links for guest teams.

## Day to day

- **Forgotten password**: a coach (or an admin, on the team page via *Team page*) makes a *New
  password link* for that person under People. The old password works until the link is used;
  older links stop working.
- **Locked out**: ten wrong passwords lock a login for 15 minutes; it unlocks by itself.
- **More videos**: *Limit* in the team's row. Empty means no limit.
- **Training yes or no changed**: the *Training OK* switch. Off: its games leave the next
  training run (the models trained before keep what they learned; retire a model if the club
  asks).
- **Someone leaves the team**: a coach removes them under People (on the newest season). They
  lose access at once and their login is deleted 30 days later. Their name stays on earlier
  seasons' rosters; to take it out of WiePlay (a request), remove them in every season too
  (the season switch at the top of the team page).
- **A new season** (PLAN §4.5): from 1 June a team row takes the matches of its own season
  only, so the team needs its next season. Its coaches do it themselves; an admin can too, from
  the team page (*Team page* in the team's row):
  1. On the team page of the newest season, *Next season* → *Start 2027/28*. Name and league
     are carried over and can be changed (promotion, a new name); the season is the next one.
  2. Tick who comes along: everyone by default; the coach starting it always comes. They keep
     their logins and see both seasons. Whoever is not ticked keeps their place on last
     season's roster but loses access at once; their login is deleted after 30 days.
  3. On the new season's page, a coach confirms again that everyone filmed is 18 or older
     (uploads wait for it), and adds the new people as usual (*Add a person*, *Invite link*).
     Someone forgotten comes back with *From earlier seasons* under People (their login works
     again if it was disabled less than 30 days ago; otherwise make them a new invite link).
  4. Last season stays as it was: its videos, tags and roster, reached with the season switch.
     Its people and links are read-only there (removing still works). The video limit counts
     the new season from zero; *Limit* and *Training OK* in the admins' list apply to every
     season of the team.
  Nothing else to do: earlier seasons' rosters go by themselves after the season after them,
  with their videos (PLAN §4.5).

## Offboard a club (or delete on request)

**Pause** (the agreement is on hold): the *Uses WiePlay* switch in its row. Every session of its
people loses the team at once and its invite links stop working. Nothing is deleted; switch it
on again to resume. Admins, too, see a switched-off team only in this list.

**End and delete** (the agreement ends, or the club asks): with the team still switched on,

1. open *Team page* and **remove its people** under People, the coaches last (an admin may
   remove anyone), in every season of the team (the season switch at the top). Their access
   ends at once, their names leave WiePlay (tags keep the number), and their logins are
   disabled, then deleted after 30 days;
2. **delete its videos** there (an admin may), in every season. The worker removes the
   originals on the NAS (`raw/<season>/vb-guest-<club>-<team>/`) and the analysis signals with
   them; the bucket objects are swept within hours;
3. **switch it off**.

What stays: the team row and the club's name (no personal data), the audit log (12 months),
and models trained on its games if it had agreed (they hold no pictures or names; retire them
if the club asks, PRIVACY §6).

Deleting a single video or a part of it, or removing a person from tags: as for KSCW teams
(PRIVACY §10), by the team's coaches or an admin.
