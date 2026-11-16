# Brief: testing WiePlay with another team

WiePlay analyses a team's volleyball match videos: it finds rallies, asks the coach about the
few it is unsure of, and builds a recap. Another club's team can try it as a **guest club**.

## What the testing team gets
- A team page on https://wieplay.kscw.ch, reached by a one-time invite link (14 days).
- Their own logins; they see only their team. KSCW members never see them.

## Before the link is sent (club admin, in the app)
1. Get the club's written OK (see [guest-club.md](guest-club.md): they film their own adult
   matches, inform their players, everyone filmed is 18+, training yes/no, video limit).
2. Admin → Teams → Guest clubs → *Create a guest team*, then copy the invite link (shown once)
   and send it to the coach directly.

## What to ask the testers to do
1. Open the link, set email and password, confirm that everyone filmed is 18 or older.
2. Add players under People (name, number, role).
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
