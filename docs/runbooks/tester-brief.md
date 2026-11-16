# Brief: testing WiePlay with another team

WiePlay analyses a team's volleyball match videos: it finds rallies, asks the coach about the
few it is unsure of, and builds a recap. Another team of our own club can try it.

## What the testing team gets
- Their own KSCW team, with the same Wiedisync login they use everywhere else. No invite link,
  no guest club and no separate agreement are needed: it is the same club.
- Coaches and team managers of the team see it at https://wieplay.kscw.ch; players see
  their own team's recaps.

## Before the test (a full admin, in the app)
1. Admin → Teams: switch the team on. WiePlay then mirrors its roster from Wiedisync on the
   next sync (run the sync from the same page to see it at once).
2. Check the roster: everyone filmed must be 18 or older. A team with under-18 players stays
   off (like DU23-1) and its matches are not filmed.
3. Tell the coach to log in with Wiedisync and open the team. Anyone else who should analyse
   (not coach) gets the *analyst* role from the coach or an admin (People → Grant analyst).

## What to ask the testers to do
1. Log in with Wiedisync and open the team page.
2. Check the roster under People (it comes from Wiedisync, so fix names there).
3. Upload one match (a date and a title). Filming tips: [FILMING_GUIDE.md](../FILMING_GUIDE.md).
4. When analysis finishes, answer the questions under "To check" (about 20 rallies), then open
   the recap.

## What we want back
- Did upload finish? How long did analysis take?
- Were the questions clear? Were rally winners and the score line right?
- Anything confusing or broken (screenshot + what they tapped).

## Known limits
- Split recordings (long matches cut into several files) are not stitched yet: upload one file.
- Without a board log or scoresheet, the score line is a guess where marked "Guess".
- Only adult leagues; no consent flow for minors.
