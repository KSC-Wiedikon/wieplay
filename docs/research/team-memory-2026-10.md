# Per-team memory and learning (October 2026)

The owner asked on 20.10.2026 that "everything gets analyzed and automatically assigned … and
save per team the players, rotation, and training". Asked what "training" means, they chose
**per-team learning**: every game a team reviews makes WiePlay better for that team, through
its players' numbers, its usual rotations and how its actions look.

This note measures what a team's earlier games can tell WiePlay about its next one, within the
privacy line, and records what was built (PLAN §10.5b).

## What prod had on 20.10.2026

- **21 videos, all of H3.** Two team rows of one lineage (2025/26 and 2026/27). No other team
  has a video yet.
- **Two typed scoresheets:**
  - game 409, H1–H3, 3 sets;
  - game 509, Legends, 4 sets.

  Together that is 7 sets with our starting six, all of them consistent.
- **Four players runs:** H1–H3 and Legends on players v3 (19.10), Volewa and Uster on v2.
- **No answers from coaches yet:**
  - no card named by a coach;
  - no tag with a number by a person;
  - no game with its rallies or actions marked as reviewed.

  So the memory starts from the sheets, and the trainings have nothing to train on.
- **Roster quirks** in Wiedisync:
  - two members on #8 (a guest and a middle blocker);
  - five on #0 (guests and coaches);
  - 18 numbers on H3's roster, of which the sheets name 13.

  The roster snapshot used to take the first member it met on a number.

## The privacy line, as built

- **Across videos only these carry over:**
  - **shirt numbers** (which of ours the team's games named, how often, in which lineups);
  - the **roster** link of a number to a member, or a coach's fix of it on the team page;
  - the **usual service orders and starting rotations**, as numbers;
  - the **models' scores** for the team.
- **Nothing about a person's looks:**
  - no faces, no face or body embedding, no crop, no colour;
  - the players job's kit colours stay inside one job run, as before (§5.7).
- **The opponents have no memory.** Their numbers stay in their video, even when the opponent
  is another KSCW team.
- **A guest team's memory is its own.** Nothing of one team informs another. A guest club's
  games still train nothing without the club's OK, its own variant included.

## 1. Rotations: the usual order as a prior where the sheet is missing

A set's starting six in zones I–VI is also its service order: zone I serves first, then
whoever stood in zone II, and so on. H3 keeps its order from set to set and from game to
game:

- setter (#7 or #17), outside #12, middle #2, opposite #1, outside #22 (or #6, #11), middle
  (#18 or #8);
- in the people per place it changes more often than in its order.

The prior (`wieplay/ml/team_prior.py`) keeps three things:

- each place of the order with the numbers seen there, turned to start at the roster's setter;
- the setter's starting zone per set (the starting rotation);
- a share for "someone else at this place" (0.2).

Per set the posterior over the rotation weighs the starting zones and every server observed.
Each place's number follows from what was read there. The scoring's serving per rally gives
our rotation at each serve: one more on every side-out we win.

**Measured** on the two typed sheets (`scripts/ml/team_memory_eval.py lineups`):

- each game in turn is the new game, and its memory comes from the other game only (another
  video);
- the truth is the sheet's server at each of our 158 serves in 7 sets;
- "readings" are simulated at the rates measured for the server label in §10.5:
  - a reading on 26 % of our serves (half are near serves; a server labelled on 77 of 90 of
    those, a number read on 47 of 77);
  - each right 87 % of the time (41 of 47), else a random roster number;
  - averaged over 300 seeds.

| Our server per serve, from | Right | Sure at p ≥ 0.9 (right) | Sure at p ≥ 0.95 (right) |
|---|---|---|---|
| The memory only (the usual starting rotation) | 42.4 % | 0 % | 0 % |
| + the set's setter zone (a coach's Sets bar entry) | 68.4 % | 0 % (at 0.8: 56 %, 83 % right) | 0 % |
| + the first server, seen once | 75.9 % | 12.7 % (100 %) | 7.6 % (100 %) |
| Readings at a quarter of our serves, **no memory** (the rotation walk alone) | 61.2 % | 25.8 % (98.5 %) | 25.8 % (98.7 %) |
| Readings at a quarter of our serves, **with the memory** | **82.2 %** | **61.2 % (97.2 %)** | **59.4 % (97.3 %)** |

- **The usual order:** 28 of 42 places right (67 %), turned at the setter. The misses are
  the people who rotate in: #6 or #11 for #22, #16 for #2, #8 or #18 at middle.
- **The rotation walk alone** (a number read on our server tells its place for the whole set)
  names 61 % of our servers. **The memory raises that to 82 %**, and the share it is sure of
  from 26 % to 61 %, at the same precision of about 97 %.
- **How many readings matter:**

  | Serves with a reading | Without the memory | With the memory |
  |---|---|---|
  | 10 % | 30.6 % | 69.3 % |
  | 26 % | 61.2 % | 82.2 % |
  | 50 % | 82.7 % | 90.5 % |

- **Thresholds chosen:**
  - a serve suggestion gets the memory's server from p ≥ 0.9;
  - the players job takes it as a server label from 0.95.

  At 0.8 a coach's setter zone alone would make 56 % of serves "sure" at only 83 % right.
- **Not measured on real readings.** The readings at our serves come from the players run: the
  card WiePlay picks at each serve contact, named by a coach or read with the team's number
  prior. Its index and pictures are in the bucket, which this session could not read. The
  evaluation runs on prod, read-only, with
  `python -m wieplay.cli team-memory --eval 848ca6e1-24bb-4799-b39e-e31857e2b5ef fe03f471-546b-4157-b54f-0d693b592e8c`.
  It prints the same rows with the real picks, and checks the recognised cards of §2 against
  the sheet's server where one of them served.

## 2. The players across videos: numbers only

A card of ours whose read number the team's earlier games named is **recognised**:

- the reader's own confidence is at least 0.6;
- with the team's number prior, the number is at least 0.95 sure (`team_prior.recognised`).

"Identify players" lists these cards apart, and one "Accept all" names them as the coach's
answers (with Undo). Every card of ours with a read number also carries the memory's
confidence, for the review's "sure" flags.

**Measured** on the prod cards of the two v3 games (`team_memory_eval.py cards`). The memory
comes from the other game's sheet. The answers are counted without by-eye labels: each answer
names its card whole, a pieces card half of its 24 biggest pieces, the most footage first. That
is an upper bound, the same for both columns.

| | H1–H3 (no calibration) | Legends (calibrated) |
|---|---|---|
| Our footage on pieces cards (no number) | 75.5 % | 56.2 % |
| On person cards with a read number | 20.7 % | 43.6 % |
| On sure cards before any answer | 0.0 % (every card capped: no calibration) | 43.5 % |
| Recognised by the memory | **8 cards, 16.0 %** (#12, #7, #22, #1, #2, #17, #18, #6) | 0 (the numbered ones are sure already) |
| Answers to name 50 % of our footage | 31 → **25** | 3 → 3 |
| Answers to name 60 % | 48 → **41** | 11 → 11 |
| Answers to name 70 % | not reachable → not reachable | not reachable |

- **Where the gain is:** an uncalibrated game, where the job is sure of no card. There the
  memory turns its numbered person cards into one confirmation, and the second game of a team
  needs about a fifth fewer answers to name half to 60 % of its footage.
- **Where there is none:** on a calibrated game the job already names those cards.
- **The precision of the recognised cards is not checked by eye** (the pictures are in the
  bucket).
  - The known risk is H1–H3: both teams wear the club kit and share #7, #12 and #22.
  - A card of H1's #12 that the job put on our side would be "recognised" as ours.
  - That is why recognised cards are offered for one confirmation, not named silently.
- **What numbers cannot do:** name the pieces cards, 56–76 % of our footage. These are the bulk
  of the answers, and nothing in the memory can link them without a look at the person.

## 3. Appearance across videos: not built; the owner decides

The only thing that would name the pieces cards of a second game is a person's appearance,
carried from game to game.

- **Within one video**, the non-biometric features the job has (the colours of head, shirt,
  shorts, legs) were measured on 19.10.2026: suggesting the numbered teammate a piece looks
  most like was right 32 % of the time (PLAN §10.5). A team shares its kit. Across halls,
  light and cameras it can only get worse.
- **A person re-identification embedding** (body shape and build, learned features) would be
  the next step. Used to recognise a person across games it is biometric data under the nDSG:
  a stored per-member template needs explicit consent. It was **not measured** (the pictures
  are in the bucket, not readable from this session) and **not built**.
- **If the owner wants to know:** a measurement that stores nothing would work like this:
  - take the numbered pieces of ours in two games of the same season, with the number as the
    truth;
  - compute an open re-identification model's embeddings in memory, on club hardware;
  - count how often a piece's nearest teammate in the other game wears its number;
  - delete everything.

  The decision to keep embeddings would come after that, with the club's privacy contact.

## 4. Per-team learning of the rally and action finders

Built into the two trainings (`train_rallies`, `train_actions`), automatic as before (queued
after a coach marks a game reviewed):

- **The rally finder.** Each team with 2 or more reviewed games is weighed on each of its games
  held out (split by match), with three models:
  - the pooled model, trained on every other game;
  - the team's games weighted 3× (`weighted`);
  - the pooled model with the team's own threshold, chosen on its other held-out games
    (`calibrated`).

  A variant is kept when it beats the pooled model by 0.01 boundary F1 and the rules on the
  team's games. The team's videos are then analysed and proposed with it.
- **The action finder** works the same way, with the team's games weighted (`weighted`),
  scored by action F1.
- **Guest teams:** a guest club's games train nothing without its OK, its own variant
  included. A KSCW team's variant may count the games of guest clubs that agreed, at weight 1,
  as the pooled model does.
- **Scores per team** go into every training's metrics (`teams`) and onto the team page.

**Measured:** nothing on prod. No game is reviewed yet, and H3 is the only team with games, so
per team and pooled are the same thing.

- **On made-up halls** (`tests/test_rally_finder.py`: a hall where the ball goes back to the
  server and nobody whistles, against halls with a referee), the pooled model already finds
  every rally. All three variants tie at boundary F1 1.0, and the pooled one is kept, as the
  margin intends.
- **Real numbers** come with the first teams that have two reviewed games each.

## 5. Number ↔ member

- **Several roster members on one number:** the snapshot takes the one who plays (not a guest
  or staff) and the team page marks the number as unclear if that does not decide it. On H3,
  #8 now goes to the middle blocker, not the guest.
- **A coach's fix on the team page** (who wears a number, or that it is none of ours) changes
  the season's games too: their roster snapshots, and our tags and cards with that number.

## Commands for prod

- Learn every team's memory now (it also refreshes by itself on use):
  `ssh hetzner "sudo docker exec wieplay-prod-api-1 python -m wieplay.cli team-memory --all"`
- Measure it on the two games with a typed sheet (read-only):
  `ssh hetzner "sudo docker exec wieplay-prod-api-1 python -m wieplay.cli team-memory --eval 848ca6e1-24bb-4799-b39e-e31857e2b5ef fe03f471-546b-4157-b54f-0d693b592e8c"`

The sheets and cards used here were read from prod with psql into the session's scratch space
and deleted after the measurement. No real numbers, names or pictures are in the repository.
