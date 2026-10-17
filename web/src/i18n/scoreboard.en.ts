// Strings of the scoreboard in the picture (docs/PLAN.md §10.3, step 5) and the game recap
// (§7.7). Registered with the other on-demand strings (src/i18n/pages.ts).

export const scoreboardEn = {
  scoreboard: {
    open: 'Mark the scoreboard',
    edit: 'Scoreboard marked',
    title: 'Mark the scoreboard',
    intro:
      'Drag a box around the score on the paused picture: both teams’ points, nothing else. Nomal reads it twice a second and uses it for the score line; where it disagrees with the serves, the rally is marked for a check. Many halls have no scoreboard in the picture: then there is nothing to mark.',
    box: 'Box',
    score: 'Score',
    sets: 'Sets won (optional)',
    setsHint: 'A second box around the sets won, if the board shows them: it tells Nomal when a new set starts.',
    kind: 'Board',
    kinds: {
      flip: 'Flip cards',
      led: 'Lit digits',
      auto: 'Not sure',
    },
    image: 'The paused picture: drag to draw the box',
    nudge: 'Arrow keys move the box, with Shift by ten pixels',
    noFrame: 'Pause the video where the scoreboard is visible, then mark it.',
    noImage: 'The picture can’t be shown here; the box is still saved where you draw it.',
    need: 'Draw a box around the score.',
    fromHall: 'The box from this hall’s last video: {{title}} ({{date}}). Check that it still sits on the score.',
    fromTeam: 'The box from the team’s last video: {{title}} ({{date}}). Check that it still sits on the score.',
    useSuggestion: 'Use the last box',
    clear: 'Remove this box',
    save: 'Save and read',
    saved: 'Saved. Nomal reads the scoreboard now; the score line follows.',
    failed: 'Couldn’t save the scoreboard: {{detail}}',
    remove: 'No scoreboard in this video',
    removed: 'The scoreboard is forgotten; the score comes from the serves again.',
    reading: 'Reading the scoreboard…',
    readingProgress: 'Reading the scoreboard… {{pct}} %',
    read_one: 'Scoreboard read: {{count}} score',
    read_other: 'Scoreboard read: {{count}} scores',
    readNothing: 'Nothing could be read on the scoreboard.',
    readFailed: 'Couldn’t read the scoreboard.',
    readAgain: 'Read again',
  },
  recap: {
    create: 'Create game recap',
    queued: 'Cutting the game recap. You’ll be told when it’s ready; find it under Downloads.',
  },
}
