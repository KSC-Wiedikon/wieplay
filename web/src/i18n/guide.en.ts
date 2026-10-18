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
  sheet: { title: string; intro: string; steps: string[]; keysTitle: string; keys: GuideKey[]; tips: string[] }
  actions: { title: string; body: string; keys: GuideKey[] }
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
      '“Accept all remaining with confidence ≥ 90%” saves time once the sure ones look right; U undoes it in one step.',
      'When the club scoreboard’s log of the game is there, “Accept all that match the scoreboard log” accepts every rally where the log has a point and WiePlay the same winner; who won stays WiePlay’s suggestion, which follows the log. Left for you: no point on the log, the log and WiePlay disagree, a rally WiePlay missed next to it, or the log says nothing. U undoes it in one step.',
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
    ],
    keysTitle: 'Keys in the service boxes',
    keys: [
      { keys: ['Tab', 'Space', '→'], text: 'Next box in the sheet’s order' },
      { keys: ['Shift + Tab', '←', 'Backspace'], text: 'Previous box (Backspace on an empty box)' },
      { keys: ['Enter'], text: 'Next box; on an empty box: the other team' },
      { keys: ['↑', '↓'], text: 'The box above or below' },
      { keys: ['*'], text: 'Circle the final' },
    ],
    tips: [
      'Names, dates of birth and licence numbers on the sheet stay on paper: WiePlay takes shirt numbers and scores only.',
      'A set that doesn’t fit is kept and used only as a hint. A set you don’t trust can be switched off; the rest still counts.',
      'Your own winners always win over the sheet: where they differ, the rally is flagged so you can look again.',
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
    cannot: ['download videos', 'delete videos', 'add other helpers'],
    privacy:
      'The videos stay in WiePlay: please don’t record or pass them on outside the club. A coach can end your access at any time; your tags and reviews stay.',
  },
}
