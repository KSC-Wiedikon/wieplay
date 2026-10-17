import type { Guide } from './guide.en'

// Swiss German spelling (ss, never ß), informal "du"; the terms of the tagging page.
export const guideDe: Guide = {
  title: 'So hilfst du',
  subtitle:
    'Für Clubmitglieder, die ein Coach als Helfer eines Teams ausgewählt hat: prüfen, was Nomal vorschlägt, und die Grundlagen taggen. Am schnellsten geht es am Computer mit Tastatur; zum Prüfen reicht ein Tablet.',
  onThisPage: 'Auf dieser Seite',
  key: 'Taste',
  meaning: 'Was sie macht',
  why: {
    title: 'Warum das zählt',
    body: [
      'Nomal findet die Ballwechsel in einem Spielvideo selbst, lernt aber von Menschen. Jedes Spiel, dessen Ballwechsel jemand geprüft und als geprüft markiert hat, wird zu einem Beispiel, aus dem Nomal lernt. So werden die Vorschläge für die nächsten Spiele besser, für alle Teams im Club.',
      'Ein geprüftes Spiel gibt dem Team ausserdem das verdichtete Spiel (nur Ballwechsel), den Spielverlauf und, mit Tags, die Statistik.',
    ],
  },
  start: {
    title: 'So fängst du an',
    steps: [
      'Ein Coach hat dich als Helfer hinzugefügt. Das Team steht jetzt auf deiner Startseite, mit dem Chip «Helfer» und den Videos aller Saisons.',
      'Öffne ein Spielvideo. Am Computer wählst du «Taggen»; um die Vorschläge von Nomal durchzugehen, stellst du den Modus auf «Vorschläge prüfen». Auf Tablet und Handy kommst du direkt zum Prüfen.',
      'Mit ? siehst du jederzeit alle Tasten.',
    ],
  },
  review: {
    title: 'Vorgeschlagene Ballwechsel prüfen',
    intro:
      'Ein Vorschlag ist ein Ballwechsel, den Nomal zu sehen glaubt, vom Aufschlag bis der Ball tot ist. Er läuft in einer Schleife ab einer Sekunde vor seinem Beginn. Du sagst, ob er stimmt.',
    steps: [
      'Schau die Schleife an. Ist es ein Ballwechsel, vom Aufschlag bis zum Ende des Spielzugs?',
      'Stimmt: Enter. Kein Ballwechsel (Einspielen, Timeout, eine Wiederholung, Spieler laufen herum): X.',
      'Beginnt oder endet zu früh oder zu spät: den Beginn mit ← → verschieben, das Ende mit Shift + ← →, dann Enter.',
      'Zwei Ballwechsel in einem Vorschlag: S teilt ihn am Abspielpunkt. Ein Ballwechsel in zwei Teilen: M verbindet ihn mit dem nächsten.',
      'Sag, wer aufgeschlagen hat (1 wir, 2 sie) und wer den Ballwechsel gewonnen hat (3 wir, 4 sie), wenn Nomal unsicher ist oder falsch liegt: Daraus ergibt sich der Spielstand.',
      'Jede Antwort geht zum nächsten Vorschlag weiter. Ein Fehler: U macht die letzte Antwort rückgängig.',
    ],
    keysTitle: 'Tasten beim Prüfen',
    keys: [
      { keys: ['Enter'], text: 'Übernehmen: ein Ballwechsel, Beginn und Ende stimmen' },
      { keys: ['X'], text: 'Ablehnen: kein Ballwechsel' },
      { keys: ['←', '→'], text: 'Beginn 0,25 s früher oder später (mit Alt: ein Bild)' },
      { keys: ['Shift + ←', 'Shift + →'], text: 'Ende 0,25 s früher oder später' },
      { keys: ['M'], text: 'Mit dem nächsten Vorschlag verbinden' },
      { keys: ['S'], text: 'Am Abspielpunkt teilen' },
      { keys: ['U'], text: 'Letzte Antwort rückgängig machen' },
      { keys: ['1', '2'], text: 'Wer hat aufgeschlagen: 1 wir, 2 sie' },
      { keys: ['3', '4'], text: 'Wer hat den Ballwechsel gewonnen: 3 wir, 4 sie' },
      { keys: ['W', 'B', 'T', 'D', 'O'], text: 'Eine Pause: Einspielen, Satzpause, Timeout, Unterbruch, andere' },
      { keys: ['↑', '↓'], text: 'Vorheriger oder nächster Vorschlag' },
      { keys: ['Space'], text: 'Abspielen oder anhalten' },
      { keys: ['Esc'], text: 'Zurück zum Taggen' },
    ],
    tips: [
      'Ungefähr reicht: eine Viertelsekunde zu früh oder zu spät schadet nicht. Ein vergessener oder erfundener Ballwechsel schon.',
      '«Alle übrigen mit Sicherheit ≥ 90 % übernehmen» spart Zeit, wenn die sicheren Vorschläge stimmen; U macht es in einem Schritt rückgängig.',
      'Unsicher? Lass den Vorschlag offen und geh mit ↓ weiter. Jemand anderes kann später entscheiden.',
    ],
  },
  done: {
    title: 'Ballwechsel als geprüft markieren',
    body: [
      'Ist jeder Vorschlag beantwortet, bietet das Prüfen «Ballwechsel als geprüft markieren» an; die Videoseite hat denselben Schalter. Erst dann lernt Nomal aus dem Spiel.',
      'Sind noch Vorschläge offen, fragt Nomal zuerst. Später einen Fehler gefunden? Korrigier ihn einfach: Das Spiel bleibt als geprüft markiert.',
    ],
  },
  tag: {
    title: 'Die Grundlagen taggen',
    intro:
      'Ein Tag sagt, wer was wie gut gemacht hat. Tipp ihn, während der Spielzug läuft (die Zeit gilt ab deiner ersten Taste), und drück Enter. Fang mit Aufschlägen und Angriffen an: Sie geben die nützlichsten Zahlen.',
    grammarTitle: 'So wird ein Tag geschrieben',
    grammar: '[a] Nummer · Aktion · [Bewertung]',
    examples: [
      { entry: '7A#', text: 'unsere #7, Angriff, Punkt' },
      { entry: 'a12S=', text: 'ihre #12, Aufschlag, Fehler (a = der Gegner)' },
      { entry: '5R+', text: 'unsere #5, Annahme, gut' },
      { entry: 'aS', text: 'ein Aufschlag von ihnen, Nummer unbekannt' },
    ],
    skillsTitle: 'Aktionen',
    skills: [
      { code: 'S', name: 'Aufschlag' },
      { code: 'R', name: 'Annahme' },
      { code: 'E', name: 'Zuspiel' },
      { code: 'A', name: 'Angriff' },
      { code: 'B', name: 'Block' },
      { code: 'D', name: 'Abwehr' },
      { code: 'F', name: 'Freeball' },
    ],
    gradesTitle: 'Bewertungen',
    grades: [
      { grade: '#', text: 'Perfekt, oder der Punkt (Ass, Angriffs- oder Blockpunkt)' },
      { grade: '+', text: 'Gut' },
      { grade: '!', text: 'OK' },
      { grade: '-', text: 'Schlecht' },
      { grade: '/', text: 'Overpass, oder geblockt (Angriff)' },
      { grade: '=', text: 'Fehler' },
    ],
    keysTitle: 'Tasten beim Taggen',
    keys: [
      { keys: ['0–9', 'a'], text: 'Einen Tag beginnen: eine Nummer, oder a für den Gegner' },
      { keys: ['Enter'], text: 'Den Tag speichern' },
      { keys: ['Tab'], text: 'Beim Tippen zwischen uns und ihnen wechseln' },
      { keys: ['Esc'], text: 'Die Eingabe abbrechen' },
      { keys: ['Space'], text: 'Abspielen oder anhalten' },
      { keys: ['Ctrl + Z'], text: 'Rückgängig' },
    ],
    tips: [
      'Eine Nummer, die in diesem Spiel niemand trägt, wird markiert, aber gespeichert: Korrigier die Aufstellung später, wenn ein Trikot anders war.',
      'Die Bewertung kann warten: 7A ist ein guter Tag; die Bewertung kommt beim nächsten Anschauen dazu.',
    ],
  },
  actions: {
    title: 'Vorgeschlagene Aktionen prüfen',
    body: 'Schlägt Nomal auch die Ballberührungen in den Ballwechseln vor (Aufschlag, Annahme, Zuspiel, Angriff…), hat das Prüfen eine zweite Liste, «Aktionen». Die Berührung läuft in einer Schleife, und du antwortest ähnlich wie bei den Ballwechseln; am Schluss markierst du auch die Aktionen als geprüft.',
    keys: [
      { keys: ['Enter'], text: 'Die Aktion übernehmen' },
      { keys: ['X'], text: 'Sie ablehnen' },
      { keys: ['S', 'R', 'E', 'A', 'B', 'D', 'F'], text: 'Es war eine andere Aktion' },
      { keys: ['#', '+', '!', '-', '/', '='], text: 'Eine Bewertung geben' },
      { keys: ['0–9', 'Enter'], text: 'Den Spieler mit der Trikotnummer nennen und übernehmen' },
    ],
  },
  rights: {
    title: 'Was Helfer können und was nicht',
    canTitle: 'Du kannst',
    can: [
      'die Videos, Tags und Clips des Teams anschauen, jede Saison',
      'die Vorschläge von Nomal prüfen, taggen, Ballwechsel markieren und Spielstände korrigieren',
      'ein Video hochladen oder verlinken und die Aufstellung eines Videos korrigieren',
      'eigene Clips und Reels anlegen, kommentieren und Clips und Reels mit dem Team teilen',
    ],
    cannotTitle: 'Du kannst nicht',
    cannot: ['Videos herunterladen', 'Videos löschen', 'weitere Helfer hinzufügen'],
    privacy:
      'Die Videos bleiben in Nomal: Bitte nimm sie nicht auf und gib sie nicht ausserhalb des Clubs weiter. Ein Coach kann deinen Zugriff jederzeit beenden; deine Tags und Prüfungen bleiben.',
  },
}
