# Official scoresheets in WiePlay

Researched and designed 18.10.2026 for `docs/PLAN.md` §10.3a. **Status: SS1 and SS2 built
19.10.2026** (what was built, what differs from this design and the measurements: PLAN §10.3a);
SS3–SS5 not yet.

**The owner's idea** (18.10.2026): "maybe also have the coaches upload the scoresheet? so at
least we can check side-out etc for points matching. if you check the scoresheet instructions,
each time a team loses a point when they're serving, we write the sum of points in the service
box. that might help the model to have the correct points no? incl. also understanding TOs,
Subs etc"

**Short answer: yes, and more than "help".** The service boxes alone give the winner and the
server of every point of every set, in order; with the lineups, timeouts and substitutions on
the same sheet, WiePlay gets both teams' rotations, who was on court, and where every break
was. What the sheet lacks is time, which the video, the scoreboard log and the serve evidence
supply. This note covers the form and its rules (§1), what OpenVolley has (§2), the algorithm
(§3), how the scoring uses it (§4), getting the sheet in (§5), and the data model, API, UI and
phases (§6).

Scores are written us:them unless a rule quotes the sheet's own order. Quotes from German
rules are followed by a short English gloss. Sections 1–7 are this note's; "plan §x" and
section numbers above 7 refer to `docs/PLAN.md`.

## Bottom line

- **Form**: H3's league (SVRZ 2. Liga in 2026/27, 3. Liga before) must use Swiss Volley's
  full official paper **Matchblatt** (the FIVB layout, in three languages) with a licensed
  scorer; no simplified sheet in 2. Liga (Volleyballreglement 2026/27 Art. 84). The 1st referee
  keeps the original and uploads a photo to VolleyManager; **each team gets a carbon copy**.
- **The rule**: when the serving team loses the serve, the scorer writes **that team's own total
  score** in the box of the player who served (Swiss rulebook Teil IV 3.1 a). The receiving team
  starts with an X in box 1 of column I; the set's finals are circled, the winner's in its last
  server's box or, if it won on reception, in the next server's box without striking its small
  number.
- **No electronic source for H3**: the eScoresheet (Genius Sports / Data Project) is used only
  in NLA and NLB; regional games in VolleyManager show set scores only, and the uploaded photo
  is not published. Wiedisync's `sets_json` is all there is.
- **OpenVolley** (read-only check): 3 indoor matches in production, none of H3 (a KSCW H1 and a
  D2 pilot, and one other clubs' match). Its 19 mirrored H3 fixtures have no match data.
- **Algorithm**: read each team's boxes in service-turn order, interleave them starting with the
  first server; each box minus the team's score when it regained the serve is the run of
  break points, each box ends with one side-out. Linear, exact (20,000 synthetic sets round
  trip), validated against the circled finals, the running score and Wiedisync's set scores;
  inconsistent sets get a suggested repair or are used only as a soft hint.
- **In WiePlay**: ranked **coach's facts > official scoresheet > scoreboard log > model**. The
  scoring aligns the sheet's points with the detected rallies (the existing dynamic programme
  with its state reduced to the sheet point); it fills `first_serve`, `start_lineup` and
  `start_rotation` per set, timeouts become `timeout` segments with their team, substitutions
  events; disagreements are flagged in Review. On synthetic sets the sheet raises the rally
  winners right from 87 % to 96 % (3 % missed and 2 % extra rallies) and to 100 % on a
  complete rally list.
- **Entry**: a typed form shaped like the sheet (**10–12 minutes per four-set match**) first; a
  photo with the video, **redacted on the phone** (since 2022 the sheet carries every player's
  date of birth), visible to coaches and admins only, deleted with the video; then automatic
  reading **on club hardware only** (per-cell digit classifiers on the Lenovo CPU, a local
  vision model on the Framework 16 as second opinion), confirmed by the coach. No external
  provider: all compliant ones cost under CHF 0.02 a sheet, but none is needed; Infomaniak is
  the fallback, Mistral's API is excluded (US-parented inference clouds).
- **Phases**: SS1 typed sheet + sheet stats, SS2 into the scoring, SS3 photo, SS4 local
  reading, SS5 optional (§6.4).

## 1. The scoresheet in H3's league

### 1.1 Which form, who has it

- **H3** plays the SVRZ (Swiss Volley Region Zürich) men's league: 3. Liga B in 2025/26 and
  2. Liga in 2026/27 (per the SVRZ schedule OpenVolley mirrors, §2).
- **The form is Swiss Volley's official paper Matchblatt**, the international FIVB-layout sheet
  (A3, German/French/Italian, Swiss Volley and FIVB logos; the SVRZ calls it "Internationales
  Matchblatt (2009)"; no form number printed). Volleyballreglement 2026/27, Art. 84 Abs. 1:
  "Für alle nationalen und regionalen Wettspiele ist grundsätzlich das offizielle Matchblatt
  von SV als Spielberichtsbogen zu verwenden. In der NLA und NLB wird ein elektronisches
  Matchblatt verwendet." Art. 84 Abs. 4: regions may allow a simplified sheet "mit Ausnahme der
  2L"; the SVRZ allows it only for 4./5. Liga and U23 strength classes 2/3 (ROW Art. 42 Abs. 3),
  and the SVRZ scorer rules say "Bei offiziellen Wettspielen der 3. Liga und höher … wird das
  offizielle Matchblatt vollständig ausgefüllt. Dafür ist eine Schreiberlizenz nötig."
  So **every H3 league match has a full official sheet, kept by a licensed scorer**.
- **What is on it besides the numbers**: both teams' rosters with names and, since 2022, **dates
  of birth** ("ab 2022: Anstelle der Lizenznummer wird das Geburtsdatum eingetragen", SVRZ
  scorer page), the coaches and officials, the remarks, and signatures of the scorer, both
  captains, both coaches (before the match) and both referees.
- **Copies and upload**: three copies; "Das Originalmatchblatt behält der (1.)
  Schiedsrichter, je einen Durchschlag erhalten die Mannschaften" (SVRZ referee guide 2026).
  The first referee uploads "ein gutes Foto bzw. Scan" to the game in VolleyManager within 48
  hours and keeps the original four weeks (ROW Art. 42 Abs. 4–5; Reglement der Unparteiischen
  Art. 10 Abs. 2; a late upload costs the club CHF 25, none CHF 50: GBO Art. 13). Both teams
  report the result within 72 hours (ROW Art. 44).
  → **Our coach always has a carbon copy** (yellow for the home team, blue for the away team,
  per the Region Basel guide) and can photograph it after the match. Carbon copies are
  fainter than the original: a point for the reading (§5.3).

### 1.2 What goes where

From Swiss Volley's "Anleitung zum Ausfüllen des Matchblattes" (Offizielle Volleyball-Regeln
2025–2028, Teil IV, printed pp. 175–193) and the FIVB "Instructions for completing the
volleyball scoresheet" (page 2 of the FIVB 2013 sheet). Swiss and FIVB agree except where
noted.

| Area | What is written | Rule |
|---|---|---|
| Toss | team A on the scorer's left; an X in circle **S** of the team that serves first and in **R** of the other; sets 2 and 4 mirrored | 2.3 a |
| Starting lineup | the six numbers from the line-up sheet in the boxes under **I–VI** (I = the server's position); time of the first serve under "Beginn" | 2.4 a–b |
| Service rounds | under each position, **8 boxes** in sets 1–4 (printed small numbers 1–8; two columns of four: 1/5, 2/6, 3/7, 4/8), **6** in a deciding set (1/4, 2/5, 3/6). When a player starts to serve, the small number of the box is **struck** (FIVB: ticked ✓); when the team loses the serve, **the team's score at that moment** is written in the box | 3.1 a, c |
| First receiving team | an **X in box 1 of column I**; its first server is the player in column II, whose small "1" is struck | 3.1 b |
| Running score ("Punkte") | 1–48 per team (1–30 in the deciding set, plus 1–8 for the left team), struck top to bottom (FIVB: slashed /) | 3.1 c |
| End of set | time under "Ende"; unused numbers struck vertically; the last point underlined (**T-bar**, Swiss only); the final score written **in the box of the winning team's last server, or of the player who would have served next, without striking its small number**; **each team's final score circled** in the service rounds | 3.1 d–f |
| Timeouts | two **T** boxes per team and set: the score, **the requesting team's score first** | 3.4 |
| Substitutions | the entering number in the box under the starting player; the score (substituting team first) in the upper score box; on the return the substitute's number is circled and the score goes in the lower box | 3.3 a–d |
| Exceptional substitution | under "Bemerkungen" (set, team, players, score), the number circled; counts in the substitution total | 3.3 e |
| Sanctions | warning / penalty / expulsion / disqualification (FIVB W/P/E/D; delay sanctions "D"), team, set, score; yellow card for the whole team; a verbal warning is not recorded | 3.5 |
| Penalty points | "Punkte, die wegen unkorrekten Verhaltens oder wegen Verzögerung des Gegners erzielt wurden (rote Karte), sind in der «Punkte»-Skala mit einem Kreis zu versehen und durchzustreichen." | 3.5 c |
| Deciding set | the left team in parts 1 and 3, the right team in part 2; as in set 1 up to 8 points; at the change of sides the last mark of the left team is copied to the same box in part 3 and its score goes in "Punkte beim Seitenwechsel", then part 3 continues; its subs and timeouts are copied too | 3.2 |
| Results | per set: timeouts, substitutions, won, points, duration; winner and "3:x" | IV |
| Libero | **not on the Matchblatt** in regional play: no libero control sheet (FIVB R-6 is international only); "Wird der Libero … ausgetauscht, so wird das nicht auf dem Matchblatt notiert" (Region Basel guide 2023) | — |

### 1.3 The service-box rule, exactly

> "Der erste Aufschlag wird durch Streichen der Hochzahl des Kästchens «1» in der Spalte des
> aufschlagenden Spielers angezeigt. Verliert die Mannschaft das Aufschlagsrecht, wird in
> dieses Kästchen die zu diesem Zeitpunkt von der aufschlagenden Mannschaft erzielte
> Gesamtpunktezahl eingetragen." (Teil IV 3.1 a, printed p. 182)

In English: the box of the player who serves gets its small number struck when the service turn
starts; when the team loses the serve, the scorer writes **that team's own total score** in it.
The owner's reading ("each time a team loses a point when they're serving, we write the sum of
points in the service box") is exactly this: it is written when the **serving** team loses a
rally, never when it wins one, and it is the serving team's score, not the opponent's.

Three marks complete it: the **X** (box 1, column I of the team that received first), the
**struck small number** (a turn started), and the **circle** (each team's final score; the
winner's final in the last server's box, or in the next server's box with the small number not
struck when the set was won on reception).

### 1.4 Exceptional cases

| Case | Rule | On the sheet |
|---|---|---|
| Wrong server / rotation fault | FIVB Rules 2025–2028, 7.7: point and serve to the opponent; found after the rally ended, "all points scored subsequently by the team at fault must be cancelled. The opponent's points remain valid. If that moment cannot be determined, no point(s) cancellation" | **Neither instruction says how cancelled points are marked.** Presumably the scorer corrects the boxes and the running score, so the sheet shows the corrected path and the video has rallies whose points do not count (§3.6; to confirm, §7 S3). USAV's manual: enter the score in the box of the correct server. |
| Penalty (red card, delay penalty) | a point and the serve to the opponent | the point circled in the opponent's running score; the sanction with its score in the sanctions box. In the service boxes it looks like any lost rally (§3.4) |
| Replay | no point | nothing written (not mentioned in either instruction) |
| Set won on reception | — | the final in the box of the player who would have served next, small number not struck |
| Deciding set | change of sides when a team reaches 8 | the left team's boxes continue in part 3 from the copied mark |
| Exceptional substitution, expulsion, disqualification | the player leaves | remarks + circled number + sanctions box |
| Libero replacements | — | not recorded |

### 1.5 Electronic scoresheets and what VolleyManager has

- **NLA and NLB** use the "eScoresheet von Genius Sports" (Data Project software; a paper sheet
  must still be on site). **1. Liga and 2.–5. Liga use paper.** SVRZ scorer rules §2.4: "In der
  NLA und NLB wird ein elektronisches Matchblatt eingesetzt (eScoresheet)." The season it
  started was not found.
- **VolleyManager / the Game Center**: an NLA game offers "Matchblatt" and "Spielstatistik"
  downloads; the Matchblatt is a one-page PDF "eScoresheet" in the FIVB layout (so its service
  boxes could be read like ours). A **regional game shows only date, venue, league, referees and
  set scores** (`hasGameScoresheet: false` on 3. Liga men's games of 2026). The referee's
  uploaded photo is internal (referee → office) and not published. The public Swiss Volley API
  Wiedisync uses returns only set results — that is what `games.sets_json` holds.
- **So for H3 there is no electronic source of the point sequence**: the team's carbon copy is
  the only one we can get.

## 2. OpenVolley: what it holds for KSCW

Checked read-only on 18.10.2026 in OpenVolley's production database (counts and field names only,
no personal data printed):

- **Indoor matches: 3 in total**, none of them H3: KSC Wiedikon H1 (2. Liga, 29.01.2026), KSC
  Wiedikon D2 (3. Liga B, 04.02.2026) and a 1. Liga women's match of two other clubs
  (10.10.2026). Two beach matches besides. The two KSCW matches look like pilots: H1 has 12
  points logged, D2 102 points over three sets of a five-set match.
- **H3 games**: OpenVolley's SVRZ schedule mirror (`svrz_games`) lists 19 H3 games (16 in
  3. Liga B 2025/26, 2 Züri Cup, 1 in 2. Liga on 23.10.2026); **none has a `matches` row**, so
  OpenVolley holds **no H3 match** of 2025/26 or 2026/27.
- **What a match there holds** (useful for the importer of §10.8 later): per point an event
  with the device timestamp (`ts`), the scoring team, the score (`score_a/score_b`), the serving
  team and server's number (`serve_team`, `serve_player`), and both lineups by position I–VI
  (`lineup_left/right`); events for `rally_start`, `timeout`, `substitution` (in, out,
  position, exceptional/expelled/disqualified), `libero_entry/exit/exchange`, `sanction` (type,
  player number, role), `replay`, `set_start/set_end` (times), `coin_toss`, `lineup`; per set
  start and end time. That is the paper sheet plus timestamps, plus what paper never has
  (libero replacements, replays).
- OpenVolley is not official for SVRZ league matches (Art. 84 requires the paper sheet), so it
  can only run beside the paper sheet, e.g. at home games where a club member scores on a
  tablet as well. Not planned.

## 3. From the service boxes to every rally

### 3.1 Why the service boxes are enough

Volleyball is rally scoring: the team that wins a rally serves the next one. A team's **service
turn** runs from the rally where it gains the serve to the rally where it loses it. The sheet
writes one number per service turn: the team's own score at the moment it lost the serve
(§1.3). Three facts make that number decisive:

1. While a team serves, every point it wins is a **break point** (won on its own serve).
2. The turn ends with one point for the **other** team: a **side-out**.
3. A team's score changes only during its own service turns and on its side-outs.

So a team that regains the serve at score *s* + 1 (its side-out point) and writes *v* when it
loses it again won exactly *v* − (*s* + 1) points on its serve in between. The two teams'
numbers, read in service-turn order and interleaved (the first server's turn 1, the other
team's turn 1, turn 2, …), give **the winner of every point of the set, in order**, who served
it, and from that each team's rotation. The running-score columns add only the totals and
which points came from a penalty; they cannot order the points between the teams.

### 3.2 The algorithm

Input per set (from §5's entry or reading):

- the first server *F* (the X in circle S; the other team has the X in box 1 of its column I);
- each team's box values **in service-turn order**: for the team that served first, turn *t*
  (0-based) is position (*t* mod 6) + 1, box ⌊*t*/6⌋ + 1; for the team that received first,
  position ((*t* + 1) mod 6) + 1, box ⌊(*t* + 1)/6⌋ + 1 (its position-I box 1 holds the X, so
  its first server is position II). "Box" is the printed small number (1–8; on paper they
  stand in two columns, 1–4 and 5–8), not the visual row. In a deciding set the left team's
  boxes continue in part 3 after the change of sides; the mark copied there at the change is
  the same box, read once;
- the six starting numbers of each team (positions I–VI);
- timeouts, substitutions, sanctions with their scores; the points circled in the running
  score (penalties).

```text
reconstruct(F, boxes[F], boxes[other(F)]):
    score = {F: start_F, other: start_other}      # 0:0 unless the set started elsewhere
    serving = F; points = []
    loop:
        v = next box of `serving`                 # fails: a box is missing
        run = v - score[serving]                  # break points of this turn
        if run < 0: inconsistent                  # the box is lower than the score
        repeat run times: points += (winner=serving, server=serving); score[serving] += 1
        if both teams' boxes are used up: return points   # the set's last box: no side-out
        points += (winner=other(serving), server=serving)   # the side-out
        score[other(serving)] += 1
        serving = other(serving)
```

The end needs no mark: the last box in turn order is the winner's final (both teams' finals
are circled on the sheet, and the loser's is an ordinary box: its score when it lost the serve
for the last time). The circles, the T-bar and whether the last box's small number is struck
(the winner served the set out) or not (won on reception) are checks, not inputs.
It is linear in the number of boxes and needs no search. A scratch check (a simulator that
plays random sets, writes the boxes the official way and reconstructs them) gave back the
exact point sequence for 20,000 random sets of 25 and 15 points.

From the point list, per point *i*: the winner *w*ᵢ, the server *s*ᵢ, the score after it, and
each team's **rotation count** *r* (side-outs it has won so far: a team rotates when it wins
the serve back). For the team that served first the server of turn *t* is starting position
(*t* mod 6) + 1 and *r* = *t*; for the team that received first the server is starting
position ((*t* + 1) mod 6) + 1 and *r* = *t* + 1. In both cases the player in zone I is the
starting position (*r* mod 6) + 1, and a player who started in position *p* stands in zone
((*p* − 1 − *r*) mod 6) + 1. WiePlay's rotation (our setter's zone, §9.7) follows: 1 → 6 → 5
→ 4 → 3 → 2 → 1, one step per side-out we win.

### 3.3 Worked example: a whole set

Set 1, 25:21 for us. **Them** served first, so **us** received first and our position-I box 1
holds the X. Synthetic numbers; our starting six (positions I–VI): 7, 3, 11, 5, 9, 1, our
setter #3 (so `start_rotation` = 2: the setter in zone 2 at the first serve).

What the scorer wrote in the service boxes (⊙ = circled final; struck small numbers not shown):

| Them (served first) | I | II | III | IV | V | VI |
|---|---|---|---|---|---|---|
| box 1 | 2 | 3 | 5 | 8 | 10 | 12 |
| box 2 | 13 | 14 | 16 | 17 | 20 | 21 ⊙ |

| Us (received first) | I | II | III | IV | V | VI |
|---|---|---|---|---|---|---|
| box 1 | X | 1 | 3 | 5 | 6 | 7 |
| box 2 | 11 | 14 | 15 | 17 | 19 | 24 |
| box 3 | 25 ⊙ (small 3 not struck) | | | | | |

In turn order: them 2, us 1, them 3, us 3, them 5, us 5, them 8, us 6, … Decoding (scores as
us:them):

| Turn | Box | Score before | Break points | Then | Score after |
|---|---|---|---|---|---|
| Them 1 (pos I) | 2 | 0:0 | 2 (0:1, 0:2) | us side-out | 1:2 |
| Us 1 (pos II, #3) | 1 | 1:2 | 0 | them side-out | 1:3 |
| Them 2 (pos II) | 3 | 1:3 | 0 | us side-out | 2:3 |
| Us 2 (pos III, #11) | 3 | 2:3 | 1 (3:3) | them side-out | 3:4 |
| Them 3 (pos III) | 5 | 3:4 | 1 (3:5) | us side-out | 4:5 |
| Us 3 (pos IV, #5) | 5 | 4:5 | 1 (5:5) | them side-out | 5:6 |
| Them 4 (pos IV) | 8 | 5:6 | 2 (5:7, 5:8) | us side-out | 6:8 |
| … | | | | | |
| Us 11 (pos VI, #1) | 24 | 20:20 | 4 (21:20 … 24:20) | them side-out | 24:21 |
| Them 12 (pos VI) | 21 ⊙ | 24:21 | 0 | us side-out | 25:21 |
| Us 12 (pos I, #7) | 25 ⊙ | 25:21 | 0 | end of set | 25:21 |

46 points, in order (U = us, T = them): TTUTU UTTUU TTTUT TUTTU UUUTU UUTUT TUUTU UTTTU UUUUT U. The last box shows the second way a set ends: we won the last point **on
reception**, so our next server's box (position I, box 3) gets the final score, circled and with
its small number not struck: #7 never served. Their final (21) is their last ordinary box,
circled too. Had #1 kept serving from 20:20 to 25:20 instead, the circled 25 would stand in
his box (position VI, box 2, small number struck) and their last box would be 20; the decoder
reads both endings the same way (the last box is the last turn, with 0 or more break
points).

The decoding also gives the set's side-out and break-point numbers without any tag: they
served 22 rallies (10 break points, and each of their 12 turns ended in our side-out), so we
sided out 12 of 22 (55 %); we served 24 rallies and won 13 of them (54 %), their side-out 11 of
24. Per rotation the same counts follow from *r* (§4.6).

### 3.4 Timeouts, substitutions, sanctions, times

Every other note on the sheet carries a **score**, and the reconstructed point list is a
monotone path through the scores, so each one lands in exactly one gap between two points:

| Sheet | Written as | On the timeline |
|---|---|---|
| Timeout | the score when it was called, the requesting team's score first (§1.2) | the gap after the point that made that score: a `timeout` segment called by that team |
| Substitution | in the column of the starting position: the entering player's number, and the score; the return with its own score | the gap after that score: the lineup on court changes from the next rally on |
| Sanction W / delay warning | type, person, score | a note in that gap, no point |
| Penalty (P, delay penalty) | type, person, score; the opponent's point circled in its running score | the point after that score is a penalty point: a point **without a rally**, and the serve goes to the opponent like any lost rally |
| Expulsion, disqualification | type, person, score | a forced substitution follows in that gap |
| Set start / end time | hh:mm | the set's span in wall-clock time, ±1 minute |

A timeout written "12:15" for them is (us 15, them 12) as us:them: the order on the sheet is
the requesting team's first, so the side of the request is needed to read it. If that score is
not on the path (a scorer who wrote it the other way round), its mirror is tried and the note
flagged.

Substitutions also give **who is on court at every rally** (libero replacements excepted:
they are not on the scoresheet, §1.2): the starting six, changed at the scores of the
substitutions, rotated by *r*. That is the jersey numbers on court per rally, for both teams
— a strong prior for player tracking (§10.5) and for the rotation charts.

### 3.5 Validation

Per set, in this order; every check names the box it doubts:

1. **Each team's boxes rise strictly** in turn order (a team regains the serve at its old box
   + 1 at least): *v*ₜ ≥ *v*ₜ₋₁ + 1, and the team that received first starts at ≥ 1.
2. **The turn counts fit**: the first server has as many boxes as the other team or one more.
3. **The decoder ends exactly on the last box**: no box left over, no box missing.
4. **The final is a legal set end**: the winner at 25 (15 in a deciding set) or more and two
   ahead.
5. **It matches the sheet's other records**: the circled finals, the last struck number (T-bar) of
   each running-score column, the set result in the results box, the penalty points circled in
   the running score against the sanctions box, the timeouts and substitutions on the path
   (§3.4), at most two timeouts and six substitutions per team.
6. **It matches the official result**: Wiedisync's set score for the game (`games.sets_json`,
   synced from Swiss Volley).

A set that passes 1–6 is **consistent** and enters the scoring as a fixed path (§4.2).

### 3.6 Inconsistent sheets

Paper sheets in regional leagues carry real mistakes (a box in the wrong column, a team's
score written in the other team's box, a crossed-out correction), and reading adds misread
digits. For a set that fails:

- **Repair search**: the cheapest set of edits that makes the set consistent with the official
  score: change one box (a misread or miswritten digit, a digit pair swapped), insert a
  missing box, drop an extra one, move a box one column. Costs come from the reading's
  confidence per cell (§5.3; for typed entries, a typo model: neighbouring keys, swapped
  digits). The search is a small dynamic programme over the boxes in turn order with the
  score as state (a set has at most ~60 boxes), so it is instant.
- **One cheapest repair** that is clearly cheaper than the next: shown to the coach as a
  suggestion ("box them IV/2 reads 13, but the score was 14 then: 15?"), applied only when
  the coach accepts it.
- **No clear repair**: the set is marked inconsistent. Its path is used only as a soft hint in
  the scoring (a lower cost than the scoreboard's, §4.2) or not at all (a switch on the sheet
  page), and the set's notes say why.
- **Rotation faults with cancelled points** (§1.4): the sheet shows them as corrections; the
  entry form has a "points cancelled" note per set (the team and the score range), which
  becomes rallies the video has but the sheet does not count (no point).
- The sheet never overrides the **official set score**: if a consistent sheet disagrees with
  Wiedisync, both are shown and the set is flagged (a results-entry error is possible on
  either side).

## 4. How WiePlay uses the sheet

### 4.1 Which source wins

| Rank | Source | Has | Lacks | Why here |
|---|---|---|---|---|
| 1 | **The coach's facts** (`won_by`, serve tags, serves called in review) | the very rally, seen on the video | coverage (only what someone tagged) | A person looked at that rally; it is the coach's analysis tool, and a coach's answer is a label. Never overwritten by anything below. |
| 2 | **The official scoresheet** | every point in order, server, rotations, lineups, TOs, subs, sanctions; signed by both captains and the referees | times: which video rally is which point is inferred | The legal record of the match, written live by a scorer and checked by the second referee. Its order of points is exact; what can go wrong is the alignment to the video (replays, cancelled points, missed rallies), which the coach's facts correct. |
| 3 | **The scoreboard log** (point-hub, being added) | a timestamp per score change | officialness: a volunteer's button presses, undo and lag, a reset between sets | It says *when*; the sheet says *what*. Where they disagree on the order, the sheet is what was signed. |
| 4 | **The model** (serve side, rally finder, scoreboard OCR) | evidence for every rally | certainty | Evidence only, never a fact (§10). |

The two middle sources are complementary rather than competing: the log anchors sheet points
to wall-clock time (and through the first serve to video time), the sheet fixes the log's
sequence and adds what the board never knew (rotations, subs, sanctions).

### 4.2 Aligning the sheet with the detected rallies

The sheet has no times; the video's rallies have no labels. Per set this is a **sequence
alignment** (Needleman–Wunsch style) between the sheet's points *P*₁…*P*ₙ and the set's
rallies *R*₁…*R*ₘ in time order. It reuses the scoring's dynamic programme (§10.3,
`wieplay/ml/scoring.py`): for a consistent sheet the state "score" can only be on the sheet's
path, so the state is simply the sheet point index *i*. Per rally:

| Move | Meaning | Cost |
|---|---|---|
| *i* → *i* + 1 | rally *Rⱼ* is point *P*ᵢ₊₁ | its serve evidence for server *s*ᵢ₊₁ (p_near and our side), the board or log for winner *w*ᵢ₊₁, a coach's serve or winner (12 to contradict) |
| *i* → *i* | *Rⱼ* is no point (a replay, a cancelled point, a proposal that is not a rally) | as today: the finder's confidence, 7 for a rally a person stands behind; free for the rallies a "points cancelled" note announces |
| *i* → *i* + *k*, then *Rⱼ* | *k* points without a rally before *Rⱼ* | a penalty point (circled, §3.4): **free and needs no gap**; any other point: a missed rally (3, cheaper in a gap with play or whistles) |

Our court side at the start of the set is still unknown to the sheet (left and right on the
sheet are not near and far in the camera), so both are tried as today; the deciding set's
change of sides happens at a known point (the leader's 8th). The sheet removes the two biggest
unknowns of the scoring — the winner and the next server of each point — and leaves only "which
rally is which point". The **serve evidence becomes an alignment signal**: the sheet's runs of
servers (them 2, us 1, them 1, us 2, …) have to line up with the near/far pattern of the
rallies, and a missed or extra rally shows as a shift in that pattern. The score log's times,
when present, pin points to moments.

Per rally the output is: the sheet point it was aligned to (or none), the sheet's winner and
server, and a confidence from the max-marginals (the cheapest alignment where this rally is
another point or no point). Flags ("check"): a rally aligned with a margin below the bar, a
sheet point with no rally that is not a penalty (a rally the finder missed: offered as "add a
rally here"), a rally without a sheet point (a replay?), and any disagreement with a coach's
fact (`score_note` "sheet").

Where the score line stands then: per rally the coach's winner, else the sheet's (aligned),
else the board's, else the model's; `score_source` says which ("tags", "sheet", "board",
"model"). With a consistent sheet and a clean alignment, **every rally's winner comes from the
sheet**, and the score line ends on the official score by construction.

**Inconsistent sheet** (§3.6): instead of the fixed path, the free scoring DP gets a cost for
each rally whose winner differs from the sheet's at that score (lower than the board's, so a
doubtful sheet cannot outvote a clear board), and the set is flagged.

Expected effect: today the evidence alone ends no set of the first real run on its official
score and flags 29 % of the rallies (§10.3). With a sheet the winners are known, and the only
errors left are alignment slips next to missed or extra rallies. A synthetic check (scratch
code, not in the repo: random sets of 25 at a 60 % side-out rate, serve sides read right 92 %
of the time, the gap before each rally with three timeouts a set, the same costs as the
scoring; our side known; no scoreboard or log) gave:

| Rally list | Winner right, official score only | Winner right, with the sheet |
|---|---|---|
| complete, no extra rallies | 90 % | 100 % |
| 3 % missed, 2 % extra (replays) | 87 % | 96 % |
| 5 % missed, 3 % extra, serve reads 85 % | 78 % | 91 % |

The errors left with a sheet sit next to a missed or extra rally (where the alignment cannot
tell which of two neighbours is the point), and most of them are flagged; a replay counts as
an error here even when only the clip, not the score line, is off. The rally finder's recall,
the score log's times and the gaps (a missed rally leaves a long one) shrink that further. To
be measured on real games once a sheet is typed in (SS2). Metrics per run
(next to today's): sheet sets used, consistent or repaired, points aligned, sheet points
without a rally (penalty / missed), rallies without a point, disagreements with the coach,
the board and the log; on reviewed games, the accuracy of the sheet's winners against the
coach's.

### 4.3 Sets

From a consistent sheet set, WiePlay fills the set's coach fields where they are empty
(never over a different value a coach entered: that is a flag):

- `period.first_serve` from S/R;
- `period.start_lineup`: our six numbers in positions I–VI (the line-up sheet's positions are
  the positions at the first serve of the set);
- `period.start_rotation`: the zone of our setter in that lineup, when the video's roster
  snapshot names exactly one setter among the six; else the coach taps the setter in the
  existing court dialog;
- `period.start_lineup_them` (new): the opponent's six numbers, by number only (§5.3 of the
  plan), so the opponent's rotation is known per rally too;
- the number of points per set for the set proposals (§10.3 "Sets"), and the set's start and
  end times as a weak hint where the video's camera time (`video.capture.creation_time`) is
  known and plausible.

These writes carry the person who confirmed the sheet as the actor, so they are revisions like
any other coach entry.

### 4.4 Timeouts, substitutions, sanctions

- **Timeouts** become `timeout` segments in the gap after the aligned point (source
  "import", `import_system` "scoresheet"), with the team that called it (new
  `segment.called_by`). A model's untouched `timeout` label in that gap is replaced; a
  coach's is kept. The gap classifier gets these as labels.
- **Substitutions** become events in that gap: a new non-skill code `SUB` in the volleyball
  taxonomy (category "admin": no grade, ignored by the box score, the regrade queue and the
  suggested actions), side, `number` = the player coming in, attributes `out` and
  `position` (I–VI), source "import". They drive "who is on court" per rally (§3.4).
- **Sanctions**: a penalty is a point without a rally (§4.2); a note on the rally list; no
  event (a card is not a volleyball action and names a person).

### 4.5 Review

In the Review tab and the rally lists:

- a **Sheet** column: the sheet point number and its winner next to the model's and the
  coach's; the score line marks rallies whose winner came from the sheet;
- flags as above, in the same list and with the same keys (`3`/`4` who won; new: `X` "no point
  (replay)", `M` "a rally is missing here");
- per set, a strip of the sheet's points (green: aligned, grey: penalty, amber: no rally
  found, red: disagreement) that scrolls with the video;
- on the sheet page, the coach can switch a set off ("don't use the sheet for set 3").

### 4.6 Stats and training

- **From the sheet alone**, even without a single tag: points, side-out % and break-point %
  per set and per rotation, for us **and the opponent** (§9.7 counts only our rotation today),
  timeouts and their effect (points in the next rallies), runs. These are official facts and
  count in the stats like a coach's rally winners; they are marked "from the scoresheet".
- **Per rally**, once aligned: the rotation walk of §9.7 no longer stops at a rally without a
  known winner.
- **Training**: an aligned, consistent sheet labels every rally's server and winner of a game
  nobody reviewed: the serve-side model (`train_rallies`) gets its labels per game, and sheet
  points without a rally are missed rallies for the rally finder (hard positives). Sheet-derived
  labels carry `source` "import" and `import_system` "scoresheet", so a dataset query can keep
  or drop them (§11).


## 5. Getting the sheet in

### 5.1 Photo or scan, with the video

- **Who**: a coach, team manager, sport admin or admin of the team (the people who may upload
  a video, §5.1 of the plan), from the video page's setup or the sheet page. Phones open the
  camera directly; a scan (PDF, JPEG, PNG, HEIC) works too. One page is the norm; a second
  one is allowed (a sheet photographed in two halves).
- **What is on it** (§1.1): both teams' names, **dates of birth**, coaches and officials,
  remarks, and signatures. WiePlay's own rules keep birthdates out of WiePlay ("The check runs
  inside Wiedisync, so birthdates never reach WiePlay", plan §5.3) and opponents to shirt
  numbers. A full photo would break both. So:
  - **Redacted on the phone, before upload.** The Matchblatt's layout is fixed, so the browser
    finds the sheet (its printed grid), shows the coach the regions it will keep — the five set
    panels (lineups, service rounds, timeouts, substitutions, running scores, times), the
    sanctions box and the results box — and **blacks out** the team lists, officials, remarks
    and signatures before a single byte leaves the phone. If the sheet is not found (a fold, a
    shadow), the coach drags the keep-boxes by hand. The original never reaches WiePlay.
  - This is a deliberate step beyond the brief (which asked for the full sheet, visible to
    coaches and admins): since 2022 the sheet carries dates of birth instead of licence
    numbers, so the full sheet is more sensitive than expected, and nothing WiePlay needs is
    in the blacked-out parts. A protest or an exceptional substitution in the remarks is typed
    into a note by the coach if it matters.
- **Stored**: private object storage next to the video's media (`sheets/<video>/<id>.jpg`),
  EXIF stripped (no GPS), re-encoded as JPEG; viewed through the API with a 5-minute signed
  URL, every view in the audit log.
- **Visible to**: coaches, team managers, sport admins and admins of that team. **Not** to
  players or helpers: they see the typed numbers and what follows from them, never the image.
- **Retention**: like the video (recording season + 1, plan §5.5), deleted with the video;
  a coach can delete the image alone at any time (the typed sheet stays).
- **Privacy page and `docs/PRIVACY.md`** gain one paragraph:

  > *Scoresheets.* For a filmed match, a coach may add a photo of the official scoresheet so
  > WiePlay knows the score of every rally. Before the upload, WiePlay blacks out the team
  > lists, dates of birth, officials, remarks and signatures on the coach's device; it keeps
  > only the score grids (shirt numbers and scores). The picture is stored with the video,
  > visible to the team's coaches and the club's admins only, read on club hardware, and
  > deleted with the video.

### 5.2 Typing it in

A form shaped like the sheet (§6.3), built for speed:

- the coach types **each team's service boxes in the sheet's own order** (column I box 1,
  column II box 1, …, then box 2 of each column): no thinking about turns, the interleaving is
  the algorithm's job. A 1–2 digit number and the cursor jumps on.
- the live check (§3.5) runs at every keystroke: the decoded score grows under the grids, a
  number that breaks a rule turns red at once, and at the end the set's final is compared with
  Wiedisync's.
- the first lineup of ours is prefilled from the last match (most sets start the same way);
  later sets copy the previous set's six.

How long it takes (a typical men's set to 25 has ~45 rallies; at a 60 % side-out rate about 28
service turns, so ~28 boxes; a simulation gives 26–31 on average, 20–37 for nine sets in
ten, at side-out rates of 55–65 %):

| Item per set | Count | Seconds |
|---|---|---|
| S/R, start and end time | 3 | 10 |
| Lineups (ours prefilled) | 6–12 numbers | 15–25 |
| Service boxes | ~28 numbers | 45–70 |
| Timeouts | 2–4 scores | 10–20 |
| Substitutions | 2–8 (number + score; returns) | 20–60 |
| Checking the red cells | | 0–30 |
| **Per set** | | **≈ 2–3½ minutes** |

**A four-set match takes about 10–12 minutes**, a five-setter 13–15. Penalties, cancelled
points and exceptional substitutions are rare and add a minute when they happen. That is short
next to a review of 190 rallies, and it can be done by anyone who may tag (a helper with the
paper copy in hand).

### 5.3 Reading the photo automatically

**The task is narrower than "OCR"**. After the redaction (§5.1) a sheet holds about 150–250
handwritten cells, almost all of them one or two digits in a box whose place on the printed form
is known, plus marks (X, struck small numbers, circles). The running-score columns need no
reading at all: their numbers are printed; only *which* are struck and which circled matters,
which is ink density in a known cell. Every reading is checked against the rules of §3.5 and the
official set scores, which catch most single errors and point at the cell.

**Pipeline** (the same whichever reader does step 3):

1. *Register* the photo to the Matchblatt template: find the printed grid lines, fit a
   homography (the sheet is flat and its layout fixed; carbon copies are faint, so contrast is
   stretched first). Already done in the browser for the redaction.
2. *Cut* each cell (lineups, service boxes, timeouts, substitutions, set times, sanctions
   scores) and each running-score number; read the marks by ink density and shape.
3. *Read* the digits: top 3 alternatives per cell with probabilities.
4. *Decode* with the rules (§3.6): the cheapest consistent sheet over the alternatives; a cell
   whose answer is not clearly the cheapest, or that the rules had to change, is "to check".
5. *Confirm*: the coach sees each doubtful cell's crop next to the value (§6.3), fixes or
   accepts; the answers are labelled examples for step 3.

**Readers compared** (research 18.10.2026; prices without VAT; "per sheet" = ten crops of
about 1500 × 1000 px, or one full redacted page):

| Option | Where the crops go | Data protection | Accuracy to expect on our cells | Cost per sheet | Fit |
|---|---|---|---|---|---|
| **Digit classifier per cell** (small CNN, trained in-domain) on the **Lenovo CPU** | nowhere | no third party | in-domain handwritten digits ~99 % per digit; a model trained only on MNIST falls to ~59 % on other people's handwriting ([ARDIS via arXiv 2004.03337](https://arxiv.org/abs/2004.03337)), so it needs our own labels (a few hundred cells, from synthetic sheets and the first typed sheets) | free; well under a second | **best**: calibrated per-cell confidence, no hallucination, tiny |
| **TrOCR-handwritten** (334 M) or **PaddleOCR-VL-1.5** (0.9 B) on the Lenovo CPU | nowhere | no third party | TrOCR: 3.4 % character error on IAM handwriting ([arXiv 2109.10282](https://arxiv.org/abs/2109.10282)); fine-tuned on our cells better; PaddleOCR-VL: no handwriting figure | free; seconds per crop | good second reader |
| **Local vision-language model** on the **Framework 16** (RTX 5070 Laptop, 8 GB): Qwen3-VL-8B or Qwen3.5-9B at 4 bit (~6–7 GB with the vision part) | nowhere (club hardware, PLAN §5.6) | no third party | Qwen3-VL-8B had the best aggregate score of 13 systems on a handwriting OCR benchmark (OmniHandwritingOCR, Aug 2026, [arXiv 2608.18586](https://arxiv.org/abs/2608.18586)); on whole handwritten documents the best model reaches ~72 % and **most errors are "prior-driven"**: the model writes what is plausible, not what is there ([WildHandBench, arXiv 2608.22959](https://arxiv.org/abs/2608.22959)) | free; estimated ~10 s per crop, 1½–2 min per sheet; queues behind the GPU's other jobs (busy guard, §8.2) | a second opinion on doubtful crops only. A plausible-but-wrong *sequence* of scores is exactly what the rules cannot catch, so a VLM never reads the service boxes alone |
| **Infomaniak AI Services** (Geneva; own Swiss data centres) | Switzerland | DPA under Swiss law (revDSG, GDPR Art. 28), no transfer outside CH/EU without consent; prompts kept only while processing, never used for training ([LLM API terms](https://welcome.infomaniak.com/api/web-components/1/cgu/latest?id=87&locale=en_GB), [DPA](https://www.infomaniak.com/documents/privacy/DPA/Accord_sur_le_traitement_des_donnees_(DPA).pdf)) | vision models listed: Qwen3.5-122B/397B, Mistral-Small-4, Ministral-3-14B, Kimi-K2.6 ([models](https://www.infomaniak.com/en/hosting/ai-services/open-source-models)); **its FAQ still says image interpretation "is being analysed"**: to test with a synthetic sheet first | Mistral-Small-4 (CHF 0.20 / 0.75 per M tokens in/out, [prices](https://www.infomaniak.com/en/hosting/ai-services/prices)): ~CHF 0.007; Qwen3.5-122B (0.40 / 3.20): ~CHF 0.017 | the only Swiss pay-per-use option with vision; fallback if local reading misses the bar |
| **IONOS AI Model Hub** (Germany) | Germany | "No third-party sub-processors", prompts "never logged", not used for training; DPA part of the contract ([data handling](https://docs.ionos.com/cloud/ai/ai-model-hub/governance-and-compliance/data-handling)) | Qwen3.5-9B/397B with images, LightOnOCR 2 ([price list](https://docs.ionos.com/cloud/support/general-information/price-list/ionos-cloud-gmbh-de)) | Qwen3.5-9B (EUR 0.10 / 0.15): ~EUR 0.002 | cleanest EU terms found |
| **OVHcloud AI Endpoints** (France) | Gravelines | DPA; "Data is not stored or shared during or after model use" ([capabilities](https://docs.ovhcloud.com/en/guides/public-cloud/ai-machine-learning/ai-endpoints-capabilities)) | Qwen3.5-9B, Qwen2.5-VL-72B and others ([catalogue](https://www.ovhcloud.com/en/public-cloud/ai-endpoints/catalog/)); no OCR model | ~EUR 0.002 (9B) to 0.023 (72B) | possible |
| **Scaleway Generative APIs** (Paris) | Paris | DPA, zero retention by default (requests that fail with a server error up to 2 weeks), no training ([data privacy](https://www.scaleway.com/en/docs/generative-apis/reference-content/data-privacy/)) | Mistral-Small-3.2, Pixtral-12B, Qwen3.x, LightOnOCR-3 ([prices](https://www.scaleway.com/en/pricing/model-as-a-service/)) | ~EUR 0.004 | possible |
| **Mistral AI** (Paris): OCR 4.1, Small 4 | EEA, but **inference sub-processors include Microsoft Azure, Google Cloud and CoreWeave** (US-parented) and Cloudflare worldwide ([sub-processors](https://trust.mistral.ai/subprocessors)) | inputs kept 30 days unless zero retention is granted on request; **training unless opted out** ([privacy policy](https://legal.mistral.ai/terms/privacy-policy)) | the strongest turnkey OCR; OCR 3 claims handwriting over printed forms ([OCR 3](https://mistral.ai/news/mistral-ocr-3/)) | OCR 4.1: USD 4 per 1,000 pages ([OCR 4.1](https://docs.mistral.ai/models/ocr-4-1)): ~USD 0.04 for ten crops | **excluded**: fails PLAN §5.6 the same way Modal did (a US control plane / US clouds) |

Nothing comparable exists ready-made: no paper, app or repository reads paper volleyball
scoresheets. The closest results: a basketball protocol reader at 70 % accuracy on numbers
([Information 2021](https://doi.org/10.3390/info12110461)), and handwritten chess scoresheets at
89 % raw, 95.6 % with the game's rules and 99.1 % when a person checks the 4 % least sure
moves ([PMC8879196](https://pmc.ncbi.nlm.nih.gov/articles/PMC8879196/)) — the pattern proposed
here: a reader, the rules, and a person for the few doubtful cells.

**Cost per season**: about 40 sheets (H3 and D1, ~20 league matches each), up to ~200 if every
KSCW volleyball team used WiePlay. Local: CHF 0. Any API above: under CHF 0.02 per sheet, so
**under CHF 5 a season** even at 200 sheets. The choice is about data protection and effort,
not money.

### 5.4 Recommendation

1. **Build the typed sheet first** (SS1, SS2): it gives the full benefit for the scoring at
   10–12 minutes per match, and it is needed anyway as the confirmation screen.
2. **Photo next** (SS3), redacted on the phone (§5.1), shown beside the form.
3. **Reading on club hardware only** (SS4): template registration and per-cell digit and mark
   classifiers on the Lenovo's CPU (free, no third party, milliseconds per cell), trained on
   synthetic sheets first and then on every confirmed cell; the rules decode; the coach checks
   the doubtful cells. Target: ≥ 98 % of cells right after decoding and ≤ 10 cells to check per
   sheet, i.e. about 1–2 minutes per match instead of 10–12.
4. **A local vision-language model on the Framework 16** (Qwen3-VL-8B or Qwen3.5-9B, 4 bit) only
   if step 3 misses the bar, and only as a second opinion on doubtful single crops.
5. **No external provider for now.** No scoresheet, crop or photo leaves the club. If local
   reading ever falls short, the next step is **Infomaniak** (Swiss data centres, Swiss-law DPA,
   no training), with crops only and after a test on a synthetic sheet, recorded as a processor
   in PLAN §5.6 and on the privacy page. **Mistral's API is excluded** (US-parented inference
   clouds, 30-day retention and training by default).

## 6. Data model, API, UI, phases

### 6.1 Tables (migration outline)

One migration (the next free number after `0019`; other branches may take `0020`):

```text
scoresheet        id uuid, video_id → video ON DELETE CASCADE (unique while not deleted),
                  game_id NULL (Wiedisync game, copied from the video),
                  us_letter 'A'|'B' NULL (which team on the sheet is us; from the team names
                  or the coach), source 'typed'|'read'|'openvolley',
                  status 'draft'|'entered'|'checked'   -- checked: a coach confirmed it
                  use_in_scoring bool default true,
                  created_by, updated_by, created_at, updated_at, deleted_at, version
scoresheet_image  id uuid, scoresheet_id → scoresheet ON DELETE CASCADE, page 1..2,
                  object_key ('sheets/<video_id>/<id>.jpg', private bucket prefix),
                  content_type, bytes, width, height, sha256, exif_stripped bool,
                  -- after the reading: the page's corners and the template it was matched to
                  registration jsonb NULL,
                  uploaded_by, uploaded_at, deleted_at
scoresheet_set    id uuid, scoresheet_id → scoresheet ON DELETE CASCADE, no 1..5,
                  first_serve 'us'|'them' NULL,
                  lineup_us int[6] NULL, lineup_them int[6] NULL,
                  boxes_us jsonb, boxes_them jsonb,
                      -- [[position 1-6, box 1-8, value int|null, mark ''|'X'|'struck'|'circled']]
                  timeouts jsonb,   -- [{side, score_req, score_other}]
                  subs jsonb,       -- [{side, position, in, out, score_req, score_other,
                                    --   kind 'sub'|'return'|'exceptional'}]
                  sanctions jsonb,  -- [{side, kind 'W'|'P'|'E'|'D'|'DW'|'DP',
                                    --   role 'player'|'staff'|'team', number NULL, score_req, score_other}]
                  penalty_points jsonb, -- running-score points circled: [{side, point}]
                  cancelled jsonb,  -- rotation fault: [{side, from_score, to_score}]
                  start_hhmm, end_hhmm NULL, final_us, final_them NULL,
                  check jsonb,      -- the validation result (§3.5): ok, issues, repairs offered
                  read jsonb NULL,  -- per cell: value, alternatives with p, crop box, accepted
                  version
```

No names, licence numbers or signatures are ever typed or read into these tables; opponents
appear by shirt number only, as everywhere in WiePlay.

Derived on read (`wieplay/core/scoresheet.py`, like the stats): the point list per set
(§3.2), the checks (§3.5), the repairs (§3.6), the sheet-only stats (§4.6). Nothing derived
is stored except what the scoring writes on the rallies.

Changes to existing tables:

```text
segment   + sheet_point int NULL      -- the sheet point this rally was aligned to (1-based)
          + sheet_won_by, sheet_serving 'us'|'them' NULL
          + called_by 'us'|'them' NULL -- a timeout's team
          score_source gains 'sheet'; score_note gains 'sheet'
period    + start_lineup_them int[6] NULL
event     + import_ref NULL           -- e.g. "sheet:set2:sub3"
taxonomy  volleyball v2: code SUB, category admin (no grade, not a skill in the stats)
```

Revisions: `scoresheet` and `scoresheet_set` get the revision trigger like segments and
periods, so a corrected box is a training label for the reader (§5.3).

### 6.2 API

| Route | Who | What |
|---|---|---|
| `GET /api/videos/{id}/scoresheet` | whoever may watch the video | the typed sheet, its checks, the point list and the sheet stats; never image URLs for players |
| `PUT /api/videos/{id}/scoresheet` | whoever may tag | the typed sheet (all sets), idempotent with `base_version` like the tagging writes; returns the checks at once |
| `POST /api/videos/{id}/scoresheet/images` | coaches, team managers, sport admins, admins | starts an upload (one signed PUT, ≤ 15 MB, JPEG/PNG/HEIC/PDF); the server strips EXIF and makes a JPEG |
| `GET /api/videos/{id}/scoresheet/images/{image}` | same | 302 to a 5-minute signed URL, audited (`scoresheet_viewed`) |
| `DELETE /api/videos/{id}/scoresheet/images/{image}` | same | soft delete; purged by the retention job |
| `POST /api/videos/{id}/scoresheet/read` | same | queues the `sheet_read` job (§5.3); the result fills `read` as proposals |
| `POST /api/videos/{id}/scoresheet/confirm` | whoever may tag | status "checked", fills the sets (§4.3), runs the scoring (reason "sheet") |
| `GET /api/videos/{id}/analysis` | as today | `scoring.sheet`: the alignment metrics (§4.2) |

Permissions in `permissions.py`: `can_see_sheet_image` (coach, team manager, sport admin,
admin of that team; not players, not helpers), `can_edit_sheet` (= `can_tag`). Audit log
entries: `scoresheet_uploaded`, `scoresheet_viewed`, `scoresheet_deleted`, `scoresheet_read`
(with where it ran).

### 6.3 UI

- **Video page → setup**: a "Scoresheet" step next to the scoreboard step: "Add the
  scoresheet (photo)" (the phone's camera opens directly), "Type it in", or "No scoresheet".
- **Sheet page** (`/videos/:id/scoresheet`), desktop and tablet: the photo on the left (zoom
  and pan; after a reading it jumps to the set's panel), the form on the right, one tab per
  set:
  - top row: S/R toggle, start and end time, the official set score from Wiedisync for
    comparison;
  - two **lineup rows** of six cells (ours prefilled from the previous set);
  - two **service grids** shaped like the sheet (six columns of 8 boxes; 6 in a deciding
    set, whose left team continues in part 3 as on paper), the X placed automatically; typing
    a number moves to the next box in the order of the printed small numbers, Enter ends a
    team's boxes;
  - TOs (two per team: two numbers each), substitutions per column (number in, score; return
    score), sanctions and "points cancelled" behind a "+ special" button;
  - a **live check line** under the grids: the decoded score as it grows ("12:9 after box
    us III/2"), the first failing check in red with the cell it doubts, the repair
    suggestion as a button;
  - after a reading, each read cell shows its value tinted by confidence; "Check 4 cells"
    jumps through the doubtful ones with the crop of that cell enlarged next to it.
- **Phones**: the same form, one team's grid at a time, a numeric keypad; the photo as a
  collapsible strip.
- **Review tab**: §4.5.
- **Stats**: a "from the scoresheet" note on numbers that came from it; the Rotations tab
  gains the opponent's rotations when a sheet is there.

### 6.4 Phases

| Phase | Contents | Size (evenings) | Done when |
|---|---|---|---|
| **SS1** Typed sheet | tables, `core/scoresheet.py` (decode, checks, repairs), `PUT/GET`, the sheet page without photo, sets filled (§4.3), sheet-only stats (§4.6) | 3–4 | a coach types a real H3 sheet in ≤ 12 minutes and the checks pass against Wiedisync |
| **SS2** Into the scoring | sheet mode in `ml/scoring.py` (§4.2), soft mode, timeouts and subs on the timeline (§4.4), flags and the Sheet column in Review, metrics | 3–4 | on the Legends game (§10.3) if its carbon copy can be found, else the next filmed H3 league match: every set ends on the official score with the sheet's winners, flagged rallies measured |
| **SS3** Photo | upload, EXIF strip, private storage, signed view, retention, audit, privacy page text, photo beside the form | 2 | a coach photographs the sheet after a match and types it next to the photo |
| **SS4** Reading (local) | template registration, cell crops, digit and mark classifiers on the Lenovo CPU, constraint decoding, "check N cells" | 4–6 | on 10 real sheets: ≥ 98 % of cells right after decoding, ≤ 10 cells to confirm per sheet |
| **SS5** Optional | the local VLM second opinion on the Framework; the OpenVolley import (§10.8) into the same tables with its point timestamps | when needed | — |

SS1 and SS2 bring the whole benefit for scoring; SS3 is small and makes SS1 pleasant; SS4 only
saves the typing.

## 7. Open questions

| # | Question | Proposal |
|---|---|---|
| S1 | The privacy contact's OK for keeping a redacted sheet photo with the video (§5.1), and the privacy page paragraph | ask before SS3 |
| S2 | Do coaches reliably get and keep the carbon copy, also at away games? | a line in the coaches' match checklist: photograph the copy after the match |
| S3 | How do SVRZ scorers mark points cancelled after a rotation fault? Neither instruction says. | ask an SVRZ scorer instructor; until then a "points cancelled" note in the form (§3.6) |
| S4 | Which teams first | H3 and D1, the teams WiePlay films now |
| S5 | Sheet-derived winners count in the stats like a coach's (§4.6)? | yes, marked "from the scoresheet"; a coach's answer still wins per rally |
| S6 | Store the opponent's starting six per set (`period.start_lineup_them`)? | yes, by number only, like their tags |

## Sources

Rules and forms (all read 18.10.2026):

- Swiss Volley, Volleyballreglement 2026/27 (Art. 84, 85, 89, 91, 93, 239):
  https://www.volleyball.ch/_Resources/Persistent/0/e/9/f/0e9f8dcf5ae32a59a5fc7f9f3f3f3ea203d7ee32/Volleyballreglement_26-27_d.pdf
- Swiss Volley, Offizielle Volleyball-Regeln 2025–2028, Teil IV "Anleitung zum Ausfüllen des
  Matchblattes" (printed pp. 175–193):
  https://www.volleyball.ch/_Resources/Persistent/7/1/1/7/71171e2ba8b1b649012440750a2fc2338d4a2b7d/Offizielle_Volleyball_Regeln_2025-2028_d%20-%20final%20inkl%20Deckblatt.pdf
- The same instructions with example figures (2013–2016 edition, hosted by the SVRZ):
  https://www.svrz.ch/_Resources/Persistent/b/3/6/0/b36088e2af9e21ed2d48354cd58e333c0e9c7983/07_Anleitung_Ausf%C3%BCllen_Matchblatt_Regelbuch_2013-2016.pdf
- FIVB International Scoresheet 2013 with its "Instructions for completing the volleyball
  scoresheet": https://www.fivb.com/wp-content/uploads/2024/02/FIVB_VB_OfficialScoresheet_2013_updated2.pdf
- FIVB Libero control sheet R-6: https://www.fivb.com/wp-content/uploads/2024/02/FIVB_R-6_LiberoPlayerControl-2011.pdf
- SVRZ Reglement für offizielle Wettkämpfe (Art. 42, 44):
  https://www.svrz.ch/_Resources/Persistent/5/e/3/a/5e3ac6df82efc003eca8bd9958f7ebda624eb1e0/SVRZ_Reglement%20f%C3%BCr%20offizielle%20Wettk%C3%A4mpfe.pdf
- SVRZ Reglement der Unparteiischen (Art. 10, 39):
  https://www.svrz.ch/_Resources/Persistent/6/c/9/b/6c9b985a09ab5edafe4a06219e4cd737fe5ddde1/SVRZ_Reglement%20der%20Unparteiischen.pdf
- SVRZ Gebührenordnung (Art. 13):
  https://www.svrz.ch/_Resources/Persistent/0/e/5/0/0e50e7cdd5c61ab180d07346e4052539d8573dfb/SVRZ_Geb%C3%BChrenordnung.pdf
- SVRZ referee guide "Spiel Vor- und Nachbereitung" 2026:
  https://www.svrz.ch/_Resources/Persistent/f/9/0/a/f90ad1f5031f103be411e34fff4aea13d5509cbf/Spiel%20Vor-%20und%20Nachbereitung_2026.pdf
- SVRZ Schreiberwesen 2026:
  https://www.svrz.ch/_Resources/Persistent/e/4/0/3/e4037be6b40bfe03039e5f6714b120bcfa15ee45/2026%20Schreiberwesen.pdf
  and the scorer page (form name, date of birth since 2022):
  https://www.svrz.ch/ausbildung/schreiberwesen/informationen
- A filled SVRZ sample sheet (3. Liga, 2025):
  https://www.svrz.ch/_Resources/Persistent/0/9/0/b/090b58d949079667574ae23ef2c638139db2d50b/Matchblatt_svrz_18082025160210.pdf
- Region Basel, "Anleitung zum Ausfüllen eines Matchblattes" 2023 (libero not recorded, copy
  colours):
  https://www.volleybasel.ch/_Resources/Persistent/1/6/8/1/1681e0ebf1b502fa297754a63ded7f3ae85cb3f9/2023_Anleitung_zum_Ausfuellen_eines_Matchblattes.pdf
- Swiss Volley, eScoresheet von Genius Sports: https://www.volleyball.ch/de/wissen/escoresheet-von-genius-sports
- Swiss Volley Game Center (an NLA game with its eScoresheet PDF):
  https://www.volleyball.ch/de/game-center/game/352421

Reading and providers: the links in §5.3's table, and
- Qwen3.5-9B model card (OCR benchmarks): https://huggingface.co/Qwen/Qwen3.5-9B
- Qwen3.5-9B GGUF sizes: https://huggingface.co/unsloth/Qwen3.5-9B-GGUF
- Mistral vision limits: https://docs.mistral.ai/capabilities/vision
- Mistral zero data retention: https://help.mistral.ai/en/articles/347612-can-i-activate-zero-data-retention-zdr
