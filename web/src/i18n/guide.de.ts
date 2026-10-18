import type { Guide } from './guide.en'

// Swiss German spelling (ss, never ß), informal "du"; the terms of the tagging page.
export const guideDe: Guide = {
  title: 'So hilfst du',
  subtitle:
    'Für Clubmitglieder, die ein Coach als Helfer eines Teams ausgewählt hat: prüfen, was WiePlay vorschlägt, und die Grundlagen taggen. Am schnellsten geht es am Computer mit Tastatur; zum Prüfen reicht ein Tablet.',
  onThisPage: 'Auf dieser Seite',
  key: 'Taste',
  meaning: 'Was sie macht',
  why: {
    title: 'Warum das zählt',
    body: [
      'WiePlay findet die Ballwechsel in einem Spielvideo selbst, lernt aber von Menschen. Jedes Spiel, dessen Ballwechsel jemand geprüft und als geprüft markiert hat, wird zu einem Beispiel, aus dem WiePlay lernt. So werden die Vorschläge für die nächsten Spiele besser, für alle Teams im Club.',
      'Ein geprüftes Spiel gibt dem Team ausserdem das verdichtete Spiel (nur Ballwechsel), den Spielverlauf und, mit Tags, die Statistik.',
    ],
  },
  start: {
    title: 'So fängst du an',
    steps: [
      'Ein Coach hat dich als Helfer hinzugefügt. Das Team steht jetzt auf deiner Startseite, mit dem Chip «Helfer» und den Videos aller Saisons.',
      'Öffne ein Spielvideo. Am Computer wählst du «Taggen»; um die Vorschläge von WiePlay durchzugehen, stellst du den Modus auf «Vorschläge prüfen». Auf Tablet und Handy kommst du direkt zum Prüfen.',
      'Mit ? siehst du jederzeit alle Tasten.',
    ],
  },
  review: {
    title: 'Vorgeschlagene Ballwechsel prüfen',
    intro:
      'Ein Vorschlag ist ein Ballwechsel, den WiePlay zu sehen glaubt, vom Aufschlag bis der Ball tot ist. Er läuft in einer Schleife ab einer Sekunde vor seinem Beginn. Du sagst, ob er stimmt.',
    steps: [
      'Schau die Schleife an. Ist es ein Ballwechsel, vom Aufschlag bis zum Ende des Spielzugs?',
      'Stimmt: Enter. Kein Ballwechsel, nur tote Zeit (Einspielen, ein Timeout, eine Wiederholung, der Ball wird übers Netz zurückgeworfen, Spieler laufen herum): X. Jedes X zeigt WiePlay, wie tote Zeit aussieht, sobald das Spiel als geprüft markiert ist.',
      'Beginnt oder endet zu früh oder zu spät: den Beginn mit ← → verschieben, das Ende mit Shift + ← →, dann Enter.',
      'Zwei Ballwechsel in einem Vorschlag: S teilt ihn am Abspielpunkt. Ein Ballwechsel in zwei Teilen: M verbindet ihn mit dem nächsten.',
      'Sag, wer aufgeschlagen hat (1 wir, 2 sie) und wer den Ballwechsel gewonnen hat (3 wir, 4 sie), wenn WiePlay unsicher ist oder falsch liegt: Daraus ergibt sich der Spielstand.',
      'Jede Antwort geht zum nächsten Vorschlag weiter. Ein Fehler: U macht die letzte Antwort rückgängig.',
    ],
    keysTitle: 'Tasten beim Prüfen',
    keys: [
      { keys: ['Enter'], text: 'Übernehmen: ein Ballwechsel, Beginn und Ende stimmen' },
      { keys: ['X'], text: 'Ablehnen: kein Ballwechsel (tote Zeit)' },
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
      'Ist das Protokoll der Club-Anzeigetafel zum Spiel da, übernimmt «Alle übernehmen, die zum Anzeigetafel-Protokoll passen» jeden Ballwechsel, für den das Protokoll einen Punkt hat und WiePlay denselben Gewinner; wer gewonnen hat, bleibt WiePlays Vorschlag, und der folgt dem Protokoll. Für dich bleiben: kein Punkt im Protokoll, Protokoll und WiePlay uneins, ein verpasster Ballwechsel daneben oder das Protokoll sagt nichts. U macht es in einem Schritt rückgängig.',
      'Unsicher? Lass den Vorschlag offen und geh mit ↓ weiter. Jemand anderes kann später entscheiden.',
    ],
  },
  done: {
    title: 'Ballwechsel als geprüft markieren',
    body: [
      'Ist jeder Vorschlag beantwortet, bietet das Prüfen «Ballwechsel als geprüft markieren» an; die Videoseite hat denselben Schalter. Erst dann lernt WiePlay aus dem Spiel: aus jedem Ballwechsel, den du behalten hast, und vor allem aus jedem Vorschlag, den du abgelehnt hast, damit es diese Art toter Zeit nicht mehr für einen Ballwechsel hält.',
      'Sind noch Vorschläge offen, fragt WiePlay zuerst. Später einen Fehler gefunden? Korrigier ihn einfach: Das Spiel bleibt als geprüft markiert.',
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
      'Die Bewertung kann warten: 7A ist ein guter Tag; «Nachbewerten» ergänzt die Bewertungen in einem zweiten Durchgang.',
    ],
  },
  regrade: {
    title: 'In einem zweiten Durchgang bewerten',
    intro:
      'Live taggt es sich leichter ohne Bewertung. Danach geht «Nachbewerten» die Tags einer Aktion durch, spielt jeden Moment ab und nimmt die Bewertung mit einer Taste, auf dem Tablet oder Handy mit einem Tippen.',
    steps: [
      'Wähl auf der Videoseite «Nachbewerten», oder stell beim Taggen den Modus auf «Nachbewerten».',
      'Wähl die Aktion (zuerst die Angriffe: Sie geben die nützlichsten Zahlen), «Nur unbewertete» oder «Alle», und wenn du willst einen Spieler oder einen Satz. Dann Starten.',
      'Jeder Tag läuft in einer Schleife. Gib die Bewertung: 1 bis 6 in der Reihenfolge der Bewertungstabelle (# + ! - / =); die Knöpfe sagen, was jede Bewertung für die Aktion heisst. 0 nimmt die Bewertung weg.',
      'Der nächste Tag kommt von selbst. S oder Enter überspringt einen, ← → blättern, U macht die letzte Bewertung rückgängig.',
    ],
    keysTitle: 'Tasten beim Nachbewerten',
    keys: [
      { keys: ['1', '2', '3', '4', '5', '6'], text: 'Die Bewertung: # + ! - / =' },
      { keys: ['0'], text: 'Keine Bewertung' },
      { keys: ['S', 'Enter'], text: 'Tag überspringen' },
      { keys: ['←', '→'], text: 'Vorheriger oder nächster Tag' },
      { keys: ['U'], text: 'Letzte Bewertung rückgängig machen' },
      { keys: ['Space'], text: 'Abspielen oder anhalten' },
      { keys: ['Esc'], text: 'Zurück zur Auswahl des Durchgangs' },
    ],
  },
  sheet: {
    title: 'Matchblatt eintragen',
    intro:
      'Jedes Meisterschaftsspiel hat ein offizielles Matchblatt, und jedes Team bekommt eine Kopie. In WiePlay abgetippt (etwa zehn Minuten für vier Sätze), liefert es jeden Punkt des Spiels der Reihe nach: wer aufschlug, wer gewann, die Rotationen beider Teams, Auszeiten und Spielerwechsel. WiePlay ordnet die Punkte dann den Ballwechseln im Video zu, so stimmen Spielstand und Statistik, ohne dass jemand einen Sieger taggt.',
    steps: [
      'Öffne auf der Videoseite «Matchblatt» (auch im Setup). Leg dir die Papierkopie bereit.',
      'Pro Satz: Wähle, wer zuerst aufschlug (das X im Kreis S), dann die Startaufstellung beider Teams über den Spalten I bis VI, wie auf dem Matchblatt. Unsere ist vom letzten Spiel ausgefüllt.',
      'Tippe die Aufschlagkästchen jedes Teams in der Reihenfolge des Matchblatts: Kästchen 1 der Positionen I bis VI, dann Kästchen 2 und so weiter. Das Team, das zuerst annahm, hat das X in Position I, Kästchen 1: Es beginnt bei II. Nach zwei Ziffern geht es von selbst weiter; Tab, Leertaste oder Enter gehen weiter.',
      'Markiere das eingekreiste Satzresultat jedes Teams mit *. Tippe die Auszeiten (der Spielstand mit dem Team, das sie verlangte, zuerst) und die Spielerwechsel (die Nummer rein, der Stand dabei, der Stand bei der Rückkehr des Startspielers).',
      'Achte auf die Prüfung über den Kästchen: Sie zeigt den Spielstand, wie er wächst, und nennt das erste Kästchen, das nicht passt. Ist ein einzelnes Kästchen falsch, schlägt WiePlay die Korrektur vor; vergleiche sie mit deiner Kopie, bevor du sie übernimmst.',
      'Speichere. Das Spiel wird neu gewertet: Jeder Satz, der zum offiziellen Resultat passt, wird den Ballwechseln zugeordnet; ein Ballwechsel, den WiePlay verpasst hat, eine Wiederholung oder ein Ballwechsel, bei dem dein Sieger vom Matchblatt abweicht, wird in der Durchsicht markiert.',
    ],
    keysTitle: 'Tasten in den Aufschlagkästchen',
    keys: [
      { keys: ['Tab', 'Leertaste', '→'], text: 'Nächstes Kästchen in der Reihenfolge des Matchblatts' },
      { keys: ['Shift + Tab', '←', 'Rücktaste'], text: 'Vorheriges Kästchen (Rücktaste auf einem leeren Kästchen)' },
      { keys: ['Enter'], text: 'Nächstes Kästchen; auf einem leeren Kästchen: das andere Team' },
      { keys: ['↑', '↓'], text: 'Das Kästchen darüber oder darunter' },
      { keys: ['*'], text: 'Satzresultat einkreisen' },
    ],
    photoTitle: 'Ein Foto des Matchblatts (Coaches)',
    photoIntro:
      'Coaches, Teamverantwortliche und Admins können neben einem Foto der Kopie tippen: Auf dem Computer steht es neben dem Formular (Mausrad zoomt, Ziehen verschiebt), auf dem Handy als Streifen über den Sätzen. Spieler/innen und Helfer sehen es nie.',
    photo: [
      'Wähle auf der Seite des Matchblatts «Foto aufnehmen» auf dem Handy oder «Foto hinzufügen» auf dem Computer.',
      'Schwärze die Geburtsdaten: Ein schwarzer Kasten liegt schon auf dem Foto. Zieh ihn über die Geburtsdaten beider Teams und an seinen Ecken auf die richtige Grösse. «Kasten hinzufügen» deckt alles Weitere ab; dreh oder schneide das Foto zu, wenn nötig.',
      'Kreuze «Die Geburtsdaten sind geschwärzt» an und lade hoch. Nur das Bild, das du siehst, verlässt dein Gerät: Das Schwarz ist eingebrannt, Ort und Zeit der Kamera fallen weg. Hast du pro Satz ein Bild gemacht, wähle den Satz dazu: Beim Tippen dieses Satzes erscheint dann sein Foto.',
      'Entferne ein Foto jederzeit mit dem Papierkorb. Es verschwindet auch, wenn das Matchblatt oder das Video gelöscht wird.',
    ],
    tips: [
      'Das abgetippte Matchblatt nimmt nur Rückennummern und Spielstände; Namen, Geburtsdaten und Lizenznummern bleiben auf dem Papier (und auf dem Foto sind die Geburtsdaten schwarz).',
      'Ein Satz, der nicht passt, bleibt gespeichert und dient nur als Hinweis. Einen Satz, dem du nicht traust, kannst du ausschalten; der Rest zählt weiter.',
      'Deine eigenen Sieger gehen dem Matchblatt immer vor: Wo sie abweichen, wird der Ballwechsel markiert, damit du nochmals hinschaust.',
    ],
  },
  actions: {
    title: 'Vorgeschlagene Aktionen prüfen',
    body: 'Schlägt WiePlay auch die Ballberührungen in den Ballwechseln vor (Aufschlag, Annahme, Zuspiel, Angriff…), hat das Prüfen eine zweite Liste, «Aktionen». Die Berührung läuft in einer Schleife, und du antwortest ähnlich wie bei den Ballwechseln; am Schluss markierst du auch die Aktionen als geprüft.',
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
      'die Vorschläge von WiePlay prüfen, taggen, Ballwechsel markieren und Spielstände korrigieren',
      'das offizielle Matchblatt des Spiels eintragen',
      'ein Video hochladen oder verlinken und die Aufstellung eines Videos korrigieren',
      'eigene Clips und Reels anlegen, kommentieren und Clips und Reels mit dem Team teilen',
    ],
    cannotTitle: 'Du kannst nicht',
    cannot: ['Videos herunterladen', 'Videos löschen', 'weitere Helfer hinzufügen', 'Fotos des Matchblatts sehen oder hinzufügen'],
    privacy:
      'Die Videos bleiben in WiePlay: Bitte nimm sie nicht auf und gib sie nicht ausserhalb des Clubs weiter. Ein Coach kann deinen Zugriff jederzeit beenden; deine Tags und Prüfungen bleiben.',
  },
}
