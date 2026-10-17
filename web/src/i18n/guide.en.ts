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
      'Right: press Enter. Not a rally (warm-up, timeout, a replay, players walking): press X.',
      'Starts or ends too early or too late: move the start with ← →, the end with Shift + ← →, then Enter.',
      'Two rallies in one suggestion: S splits it at the playhead. One rally cut in two: M merges it with the next one.',
      'Say who served (1 us, 2 them) and who won the rally (3 us, 4 them) when WiePlay is unsure or wrong: the score line follows from it.',
      'Each answer moves on to the next suggestion. A mistake: U undoes the last answer.',
    ],
    keysTitle: 'Keys while reviewing',
    keys: [
      { keys: ['Enter'], text: 'Accept: a rally, and its start and end are right' },
      { keys: ['X'], text: 'Reject: not a rally' },
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
      'Not sure? Leave it open and move on with ↓. Someone else can decide later.',
    ],
  },
  done: {
    title: 'Mark the rallies reviewed',
    body: [
      'When every suggestion is answered, the review offers “Mark rallies reviewed”; the video page has the same switch. Only then does WiePlay learn from the game.',
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
      'A grade can wait: 7A is a fine tag; the grade can be added when you watch again.',
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
      'upload or link a video, and correct a video’s roster',
      'keep your own clips and reels, comment, and share clips and reels with the team',
    ],
    cannotTitle: 'You can’t',
    cannot: ['download videos', 'delete videos', 'add other helpers'],
    privacy:
      'The videos stay in WiePlay: please don’t record or pass them on outside the club. A coach can end your access at any time; your tags and reviews stay.',
  },
}
