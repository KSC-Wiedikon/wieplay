// The guide for team helpers (/how-to-help, PLAN §5.1 "analyst"). Plain text per language,
// loaded with the guide page only; src/pages/GuidePage.tsx picks the reader's language.

export interface GuideKey {
  keys: string[]
  text: string
}

export interface Guide {
  title: string
  subtitle: string
  onThisPage: string
  key: string
  meaning: string
  why: { title: string; body: string[] }
  start: { title: string; steps: string[] }
  review: { title: string; intro: string; steps: string[]; keysTitle: string; keys: GuideKey[]; tips: string[] }
  done: { title: string; body: string[] }
  tag: {
    title: string
    intro: string
    grammarTitle: string
    grammar: string
    examples: { entry: string; text: string }[]
    skillsTitle: string
    skills: { code: string; name: string }[]
    gradesTitle: string
    grades: { grade: string; text: string }[]
    keysTitle: string
    keys: GuideKey[]
    tips: string[]
  }
  regrade: { title: string; intro: string; steps: string[]; keysTitle: string; keys: GuideKey[] }
  sheet: {
    title: string
    intro: string
    steps: string[]
    keysTitle: string
    keys: GuideKey[]
    photoTitle: string
    photoIntro: string
    readTitle: string
    readIntro: string
    read: string[]
    photo: string[]
    tips: string[]
  }
  actions: { title: string; body: string; keys: GuideKey[] }
  identify: { title: string; intro: string; steps: string[]; keysTitle: string; keys: GuideKey[]; tips: string[] }
  rights: { title: string; canTitle: string; can: string[]; cannotTitle: string; cannot: string[]; privacy: string }
}

export const guideEn: Guide = {
  title: 'How to help',
  subtitle:
    'For club members a coach picked to help a team: review what WiePlay suggests, and tag the basics. A computer with a keyboard is fastest; a tablet works for reviewing.',
  onThisPage: 'On this page',
  key: 'Key',
  meaning: 'What it does',
  why: {
    title: 'Why it matters',
    body: [
      'WiePlay finds the rallies in a game video by itself, but it learns from people. Every game whose rallies someone checked and marked reviewed becomes an example WiePlay trains on, so the suggestions for the next games get better, for every team in the club.',
      'A reviewed game also gives the team its condensed match (rallies only), the score line and, with tags, the statistics.',
    ],
  },
  start: {
    title: 'Getting started',
    steps: [
      'A coach added you as a helper. The team now shows on your start page with a “Helper” chip, with every season of its videos.',
      'Open a game video. On a computer, choose “Tag”; to go through WiePlay’s suggestions, switch the mode to “Review suggestions”. On a tablet or phone you get the review straight away.',
      'Press ? at any time for every key.',
    ],
  },
  review: {
    title: 'Review rally suggestions',
    intro:
      'A suggestion is a rally WiePlay thinks it saw, from the serve to the moment the ball is dead. It plays in a loop from a second before it starts. You say whether it is right.',
    steps: [
      'Watch the loop. Is it one rally, from the serve to the end of play?',
      'Right: press Enter. Not a rally, just dead time (warm-up, a timeout, a replay, the ball thrown back over the net, players walking): press X. Every X teaches WiePlay what dead time looks like, once the game is marked reviewed.',
      'Starts or ends too early or too late: move the start with ← →, the end with Shift + ← →, then Enter.',
      'Two rallies in one suggestion: S splits it at the playhead. One rally cut in two: M merges it with the next one.',
      'Say who served (1 us, 2 them) and who won the rally (3 us, 4 them) when WiePlay is unsure or wrong: the score line follows from it.',
      'Each answer moves on to the next suggestion. A mistake: U undoes the last answer.',
      'WiePlay weighs everything it has for each rally at once: the video, the club scoreboard’s log, the scoresheet (typed, or read from its photo), the official result, the timeouts, substitutions and cards. A rally it is sure of needs no one: “Accept all sure rallies” accepts them in one step. The list shows “Needs you” first: only the rallies it stays unsure of, each with why (“the video and the sheet disagree on the winner”, “the sheet’s box here is unclear (17 or 11)”, “no point on the board log”, “a timeout at 14:12 doesn’t fit here”, “a missed rally next to it”).',
      'Decide those: who won (3 4), who served (1 2), reject (X) or a break (W B T D O). Each decision is scored again at once: the rallies around it often become sure, and a scoresheet box behind it resolves by itself, with no separate check on the sheet page.',
    ],
    keysTitle: 'Keys while reviewing',
    keys: [
      { keys: ['Enter'], text: 'Accept: a rally, and its start and end are right' },
      { keys: ['X'], text: 'Reject: not a rally (dead time)' },
      { keys: ['←', '→'], text: 'Move the start 0.25 s earlier or later (with Alt: one frame)' },
      { keys: ['Shift + ←', 'Shift + →'], text: 'Move the end 0.25 s earlier or later' },
      { keys: ['M'], text: 'Merge with the next suggestion' },
      { keys: ['S'], text: 'Split at the playhead' },
      { keys: ['U'], text: 'Undo the last answer' },
      { keys: ['1', '2'], text: 'Who served: 1 us, 2 them' },
      { keys: ['3', '4'], text: 'Who won the rally: 3 us, 4 them' },
      { keys: ['W', 'B', 'T', 'D', 'O'], text: 'A break: warm-up, set break, timeout, stoppage, other' },
      { keys: ['↑', '↓'], text: 'Previous or next suggestion' },
      { keys: ['Space'], text: 'Play or pause' },
      { keys: ['Esc'], text: 'Back to tagging' },
    ],
    tips: [
      'Being close is enough: a quarter of a second either way does no harm. A missed or invented rally does.',
      '“Accept all sure rallies” confirms their start and end; who won stays WiePlay’s suggestion, which follows the evidence. U undoes it in one step. A match without the board’s log or a scoresheet has no sure rallies; there “Accept the rallies WiePlay is 90 % sure of” confirms the rallies’ start and end, and who won stays under “Needs you”.',
      'Sure means sure: on the games WiePlay was checked on, not one rally it was sure of was wrong. A game with only the video and the official result has few sure rallies, one with the scoreboard’s log or the scoresheet many more: that is honest, not a fault.',
      'Not sure? Leave it open and move on with ↓. Someone else can decide later.',
    ],
  },
  done: {
    title: 'Mark the rallies reviewed',
    body: [
      'When every suggestion is answered, the review offers “Mark rallies reviewed”; the video page has the same switch. Only then does WiePlay learn from the game: from every rally you kept and, most of all, from every suggestion you rejected, so it stops mistaking that kind of dead time for a rally.',
      'If suggestions are still open, WiePlay asks first. Found a mistake later? Just correct it: the game stays marked reviewed.',
    ],
  },
  tag: {
    title: 'Tag the basics',
    intro:
      'A tag says who did what, and how well. Type it while the play happens (the time is taken at your first key) and press Enter. Start with serves and attacks: they make the most useful numbers.',
    grammarTitle: 'How a tag is written',
    grammar: '[a] number · skill · [grade]',
    examples: [
      { entry: '7A#', text: 'our #7 attack, kill' },
      { entry: 'a12S=', text: 'their #12 serve, error (a = the opponent)' },
      { entry: '5R+', text: 'our #5 reception, good' },
      { entry: 'aS', text: 'a serve by them, number unknown' },
    ],
    skillsTitle: 'Skills',
    skills: [
      { code: 'S', name: 'Serve' },
      { code: 'R', name: 'Reception' },
      { code: 'E', name: 'Set' },
      { code: 'A', name: 'Attack' },
      { code: 'B', name: 'Block' },
      { code: 'D', name: 'Dig' },
      { code: 'F', name: 'Freeball' },
    ],
    gradesTitle: 'Grades',
    grades: [
      { grade: '#', text: 'Perfect, or the point (ace, kill, block point)' },
      { grade: '+', text: 'Good' },
      { grade: '!', text: 'OK' },
      { grade: '-', text: 'Poor' },
      { grade: '/', text: 'Overpass, or blocked (attack)' },
      { grade: '=', text: 'Error' },
    ],
    keysTitle: 'Keys while tagging',
    keys: [
      { keys: ['0–9', 'a'], text: 'Start a tag: a number, or a for the opponent' },
      { keys: ['Enter'], text: 'Save the tag' },
      { keys: ['Tab'], text: 'Switch between us and them while typing' },
      { keys: ['Esc'], text: 'Cancel the entry' },
      { keys: ['Space'], text: 'Play or pause' },
      { keys: ['Ctrl + Z'], text: 'Undo' },
    ],
    tips: [
      'A number nobody wears in this game is marked, but saved: fix the roster later if a shirt was different.',
      'A grade can wait: 7A is a fine tag; the regrade queue adds the grades in a second pass.',
    ],
  },
  regrade: {
    title: 'Grade in a second pass',
    intro:
      'Tagging live is easier without grades. Afterwards the regrade queue goes through the tags of one skill, plays each moment and takes the grade with one key, on a tablet or phone with a tap.',
    steps: [
      'On the video page choose “Regrade”, or switch the mode to “Regrade” while tagging.',
      'Pick the skill (attacks first: they make the most useful numbers), “Ungraded only” or “All of them”, and if you like one player or one set. Then Start.',
      'Each tag plays in a loop. Give the grade: 1 to 6 in the order of the grades table (# + ! - / =); the buttons say what each grade means for the skill. 0 takes the grade away.',
      'The next tag comes by itself. S or Enter skips one, ← → go back and forth, U undoes the last grade.',
    ],
    keysTitle: 'Keys while regrading',
    keys: [
      { keys: ['1', '2', '3', '4', '5', '6'], text: 'The grade: # + ! - / =' },
      { keys: ['0'], text: 'No grade' },
      { keys: ['S', 'Enter'], text: 'Skip the tag' },
      { keys: ['←', '→'], text: 'Previous or next tag' },
      { keys: ['U'], text: 'Undo the last grade' },
      { keys: ['Space'], text: 'Play or pause' },
      { keys: ['Esc'], text: 'Back to the choice of the pass' },
    ],
  },
  sheet: {
    title: 'Enter the scoresheet',
    intro:
      'Every league match has an official scoresheet, and each team gets a copy. Typed into WiePlay (about ten minutes for four sets), it gives every point of the match in order: who served, who won, both teams’ rotations, timeouts and substitutions. WiePlay then lines the points up with the video’s rallies, so the score line and the statistics are right without anyone tagging a winner.',
    steps: [
      'On the video page open “Scoresheet” (also in the setup). Have the paper copy in front of you.',
      'Per set: choose who served first (the X in circle S), then the starting six of both teams, above the columns I to VI, as on the sheet. Ours are filled in from the last match.',
      'Type each team’s service boxes in the sheet’s own order: box 1 of positions I to VI, then box 2, and so on. The team that received first has the X in position I box 1: start at II. Two digits move on by themselves; Tab, Space or Enter move on.',
      'Mark each team’s circled final with *. Type the timeouts (the score with the team that asked first) and the substitutions (the number in, the score then, the score when the starting player came back).',
      'Watch the check above the boxes: it shows the score as it grows and names the first box that doesn’t fit. If a single box is off, WiePlay suggests the fix; check it against your copy before you apply it.',
      'Save. The match is scored again: each set that fits the official result lines up with the rallies; a rally WiePlay missed, a replay, or a rally where your winner differs from the sheet is flagged in the review.',
      'If the club scoreboard’s log and the video show that the scorer slipped (a missed side-out, a run written under the wrong server, caught up later: the set still adds up), the score line follows them on those few points and the rest of the set follows the sheet. Those rallies wait for you under “Needs you” (“the scoresheet and the board log disagree here; the video sides with the board log”), and the sheet page names the boxes they say otherwise (“Volero’s box III/1 is 2, not 5”), marks them in red and offers “Apply the correction”: a new version of the sheet, the paper’s kept in its history.',
    ],
    keysTitle: 'Keys in the service boxes',
    keys: [
      { keys: ['Tab', 'Space', '→'], text: 'Next box in the sheet’s order' },
      { keys: ['Shift + Tab', '←', 'Backspace'], text: 'Previous box (Backspace on an empty box)' },
      { keys: ['Enter'], text: 'Next box; on an empty box: the other team' },
      { keys: ['↑', '↓'], text: 'The box above or below' },
      { keys: ['*'], text: 'Circle the final' },
    ],
    photoTitle: 'A photo of the sheet (coaches)',
    photoIntro:
      'Coaches, team managers and admins can type next to a photo of the copy: it shows beside the form on a computer (zoom with the wheel, drag to move) and as a strip above the sets on a phone. Players and helpers never see it.',
    photo: [
      'On the scoresheet page choose “Take a photo” on a phone, or “Add a photo” on a computer.',
      'Black out the dates of birth and licence numbers: two black boxes are already on the photo, over the team lists and over the officials’ block (the referees’ and scorer’s licence numbers are often dates of birth too). Drag them into place and pull their corners to size them. “Add a box” covers anything else, such as a name in the remarks; turn or crop the photo if you need to. Leave the sets, the sanctions and the result visible: WiePlay reads them.',
      'A scan as a PDF works too: choose it like a photo. Its pages are drawn on your device and each goes through the same step; only the blacked-out picture is uploaded, never the PDF.',
      'Tick “The dates of birth and licence numbers are blacked out” and upload. Only the picture you see leaves your device: the black is burned in, and the camera’s place and time are dropped. If you photographed one set per picture, say which set it shows: typing that set shows that photo.',
      'Remove a photo with the bin at any time. It also goes when the scoresheet or the video is deleted.',
    ],
    readTitle: 'Let WiePlay read the photo',
    readIntro:
      'WiePlay reads the photo on the club’s own server (no outside service sees it): it finds the printed grid, reads every box, lineup, timeout and substitution, and checks them against the rules and the official result. You only check the cells it isn’t sure of: a few minutes instead of ten.',
    read: [
      'Add the photo while nothing is typed yet: it is read by itself within a few seconds. Otherwise press “Read the photo”. What you typed is never replaced.',
      'The sets read from the photo are drafts: the score line doesn’t use them yet. “Read from the photo: check N cells” counts the doubtful ones; they are marked yellow, the sure ones dashed.',
      'Press “Next cell to check” (or click a yellow cell): the photo zooms to that cell. If the value is right, press “It is right”; if not, type the right one.',
      'Press “Accept the sure cells”, then “Confirm set N”. A confirmed set is a typed set like any other: the match is scored again with it.',
      'If WiePlay took the wrong team for yours, switch with “We are team A/B”. A set the rules couldn’t make fit (boxes the scorer left empty, a smudged copy) is shown as read, nothing filled in: what the reader wasn’t sure of, or what breaks the order of the scores, is to check; or type it yourself.',
    ],
    tips: [
      'The typed sheet takes shirt numbers and scores only; names, dates of birth and licence numbers stay on paper (and on the photo they are black).',
      'The sheet is strong evidence, not the last word: it wins against the video alone, it and the board log weigh about alike, and where they disagree the video decides. Every rally where they disagree needs you, whichever way the score line went.',
      'A set that doesn’t fit is kept and used only as a hint. A set you don’t trust can still be switched off (“Use this set for the score”), but that is rarely needed now: where the log and the video disagree with the sheet, they win those points anyway.',
      'Your own winners always win, over the sheet too: the rallies next to one that differs from the sheet are flagged so you can look again.',
      'A serve you tag without a number in a rally the sheet is lined up with gets the server the sheet names (aS for theirs); a number you type always wins. “Apply” on the players page fills the serves you tagged before.',
      'The statistics show the sheet’s numbers per player even without a single tag: serves, points won on them, the longest run, aces and errors where a serve tag graded them, and the plus/minus on court, per rotation too.',
      'Timeouts, substitutions and cards are checked at their break in the video: the review’s “From the scoresheet” lists those where the score line or the scoreboard’s log says otherwise, and the cards are marked on the timeline (the team only, never a person).',
      'Finding the players uses the sheet too: the server gets the number of zone I, and a player whose shirt shows a number only the other team has on court then moves to that team.',
    ],
  },
  actions: {
    title: 'Review suggested actions',
    body: 'When WiePlay also suggests the touches inside the rallies (serve, reception, set, attack…), the review has a second queue, “Actions”. The contact plays in a loop and you answer much like for rallies; at the end, mark the actions reviewed too.',
    keys: [
      { keys: ['Enter'], text: 'Accept the action' },
      { keys: ['X'], text: 'Reject it' },
      { keys: ['S', 'R', 'E', 'A', 'B', 'D', 'F'], text: 'It was another skill' },
      { keys: ['#', '+', '!', '-', '/', '='], text: 'Give it a grade' },
      { keys: ['0–9', 'Enter'], text: 'Name the player by shirt number, and accept' },
    ],
  },
  identify: {
    title: 'Identify players',
    intro:
      'After “Find players”, WiePlay knows some people for sure (their number read often enough); the rest wait for you. “Identify players (N to check)” on the video page shows them one at a time, the one seen longest first, large: the video zoomed onto the person at several moments.',
    steps: [
      'Tap their number. Ours come from the roster; the numbers the scoresheet has on court in that card’s rallies come first. Theirs: tap or type the number.',
      'Not a player (a referee, a line judge, the bench), the other team (this card only), or the same person as another card: one tap each.',
      'Several people on one card (WiePlay joined them by their colours): tick the pictures of one person and tap their number; repeat for the others.',
      'Not sure? Skip. Undo takes back the last answer.',
      'While watching: “Name a player” under the video, pause, tap a person and pick their number.',
      'If WiePlay can’t tell the teams apart (no court set, similar kits), it first asks which side of the net was ours in the first set.',
    ],
    keysTitle: 'Keys while identifying',
    keys: [
      { keys: ['0–9', 'Enter'], text: 'The number' },
      { keys: ['N'], text: 'Not a player' },
      { keys: ['T'], text: 'The other team' },
      { keys: ['S'], text: 'Same as another card' },
      { keys: ['M'], text: 'Several people: name pieces' },
      { keys: ['K', '→'], text: 'Skip' },
      { keys: ['U'], text: 'Undo' },
    ],
    tips: [
      'Each answer is a fact: cards whose shirt shows the number you gave join that card by themselves.',
      'You match the person with your own eyes; WiePlay uses no faces, and nothing leaves this video.',
      'Far-side players at 1080p are often too small to tell: skip them, the near side matters most.',
    ],
  },
  rights: {
    title: 'What helpers can and can’t do',
    canTitle: 'You can',
    can: [
      'watch the team’s videos, tags and clips, every season',
      'review WiePlay’s suggestions, tag, mark rallies and correct scores',
      'type in the match’s official scoresheet',
      'upload or link a video, and correct a video’s roster',
      'keep your own clips and reels, comment, and share clips and reels with the team',
    ],
    cannotTitle: 'You can’t',
    cannot: ['download videos', 'delete videos', 'add other helpers', 'see or add photos of the scoresheet'],
    privacy:
      'The videos stay in WiePlay: please don’t record or pass them on outside the club. A coach can end your access at any time; your tags and reviews stay.',
  },
}
