# Filmanleitung

Für Coaches und Teamverantwortliche der Teams, die WiePlay nutzen. Englische Fassung:
[FILMING_GUIDE.md](FILMING_GUIDE.md). Technischer Hintergrund: `docs/PLAN.md` §5.3, §5.4,
§7.1 und §7.2.

## 1. Bevor die Kamera aufgestellt wird

- **Nur Spiele.** WiePlay nimmt Spiele: die Spiele des Teams in Wiedisync und andere Spiele
  (Freundschaftsspiele, Turniere, Cupspiele) mit Datum und Titel. Trainings werden vorerst
  nicht für WiePlay gefilmt.
- **WiePlay prüfen.** Öffne dein Team in WiePlay. Unter *Filmen* steht für jedes Spiel der
  nächsten 21 Tage **Filmen erlaubt** oder **Filmen gesperrt**, mit Grund. **Gesperrt heisst:
  keine Kamera.** WiePlay nimmt das Video sonst auch nicht an. Andere Spiele stehen dort nicht;
  es gelten dieselben Regeln, und WiePlay prüft sie beim Hochladen anhand des Datums.
- **Alle sind volljährig.** WiePlay nimmt Aufnahmen nur an, wenn alle Personen im Kader an
  diesem Tag 18 oder älter sind und in Wiedisync ein Geburtsdatum haben. Das wird
  automatisch geprüft und lässt sich nicht übersteuern.
- **Die Filmregel des Teams.** Ein Admin kann das Filmen für ein Team ausschalten. WiePlay
  zeigt dann *Spiele dieses Teams werden nicht gefilmt.*
- **Keine Einwilligung nötig.** Spielerinnen und Spieler werden nicht einzeln gefragt. Das
  Reglement von Swiss Volley erlaubt es allen, ein Spiel aufzunehmen (Volleyballreglement
  2026/27, Art. 86), und der Club filmt seine Spiele für WiePlay auf dieser Grundlage. Die
  Spielerinnen und Spieler erfahren es über die Datenschutzseite von WiePlay. Wer nicht in
  einem Video sein will, meldet sich bei einem Coach oder einem WiePlay-Admin; das Video wird
  gelöscht oder die Person aus den Tags entfernt.
- **Gegnerinnen und Gegner.** In Erwachsenenligen filmt der Club, ohne jeden Gegner zu
  fragen. Sie erscheinen nur mit der Rückennummer. Auf Wunsch wird gekürzt oder gelöscht.

## 2. Wo die Kamera steht

### Volleyball: hinter dem Feld

- Stativ **mittig hinter der Grundlinie**, **2,5 bis 3 m hoch** (mindestens 1,5 m).
- **Ganzes Feld und etwa 2 m rundherum** im Bild, **alle vier Ecken der hinteren Hälfte**
  sichtbar.
- **Fester Zoom.** Während des Spiels nicht bewegen, schwenken oder zoomen.
- **Kein Weitwinkel, kein Fischauge.**
- Anzeigetafel im Bild, wenn das ohne Feldverlust geht.
- **Auswärtsspiele**: Das Heimteam stellt einen Kameraplatz hinter dem Feld bereit, mittig,
  etwa 3 m hoch (Volleyballreglement, Art. 86). Vor dem Einspielen danach fragen.

### Basketball (Herren 1): seitlich

- An der **Längsseite auf Höhe der Mittellinie**, so hoch wie möglich (Galerie oder oberste
  Tribünenreihe), **beide Körbe im Bild**.
- Fest, nicht schwenken. Ist die Halle zu schmal, ist ein leichter Weitwinkel in Ordnung,
  nie Fischauge.

## 3. Kameraeinstellungen

| Einstellung | Wert | Warum |
|---|---|---|
| Auflösung, Bildrate | mindestens **1080p mit 50 Bildern/s**; **4K mit 50** wenn möglich | Details im hinteren Feld; 50 Bilder/s für schnelle Aktionen |
| Nicht 60 Bilder/s | **50** | Hallenlicht flackert mit 50 Hz; bei 60 kann man es sehen |
| Verschlusszeit | **1/500 s oder kürzer**; länger nur bei Flackerstreifen | Bewegungsunschärfe macht den Ball unsichtbar |
| Belichtung, Fokus, Weissabgleich | **fest (manuell)** | Automatiken pumpen während des Spiels |
| Ton | **an** | WiePlay findet die Ballwechsel auch an den Pfiffen |

WiePlay liest das alles aus der Datei. Ein Video unter dem Standard (z. B. 25 Bilder/s oder
720p) lässt sich abspielen und taggen, die automatische Analyse überspringt es aber.

## 4. Während des Spiels

- Ab dem **Einspielen** und **durchgehend** filmen. Zwischen den Sätzen nicht pausieren.
- Kameras teilen lange Aufnahmen in mehrere Dateien. Kein Problem: alle als ein Video
  hochladen, WiePlay fügt sie zusammen.
- Speicher: ein 2-Stunden-Spiel in 1080p50 braucht etwa **25 bis 45 GB**. Akku geladen oder
  Netzteil, leere Karte.
- Kamera auf das Feld richten, nicht auf die Tribüne.

## 5. Das Video in WiePlay bringen

Auf der Teamseite: *Video hochladen* → das Spiel wählen, oder *Anderes Spiel
(Freundschaftsspiel, Turnier, Cup)* mit Datum und Titel → wählen, woher das Video kommt:

- **Von diesem Gerät.** Alle Dateien der Aufnahme wählen → *Upload starten*. Seite offen
  lassen, bis es fertig ist. Ein unterbrochener Upload geht weiter, wenn du auf der
  Videoseite dieselben Dateien nochmals wählst.
- **Aus der NAS-Inbox** (für alle mit Zugang zum NAS des Clubs). Die Kameradateien in den
  Ordner deines Teams im Ordner `inbox` der Freigabe `wieplay` kopieren (zum Beispiel `inbox/vb-H3`). Nach ein
  paar Minuten erscheinen sie unter *Aus der NAS-Inbox*. Auswählen → *Video hinzufügen*. Sie
  werden nicht nochmals hochgeladen.
- **Über einen Link.** Ist das Spiel schon auf YouTube oder Twitch, auf dem Kanal des Clubs
  oder dem eigenen Kanal eines Mitglieds? Den Link des Videos einfügen → *Video importieren*,
  WiePlay holt es selbst. Es gehen nur Kanäle auf der Liste des Clubs (ein Admin fügt sie unter
  *Admin → Import-Kanäle* hinzu). Videos auf YouTube als *Nicht gelistet* hochladen: Wer den
  YouTube-Link hat, kann sie dort ansehen. Twitch löscht vergangene Übertragungen nach 7 bis
  60 Tagen, also bald nach dem Spiel importieren. Die Originaldateien der Kamera haben eine
  bessere Qualität, falls du sie hast.

WiePlay verarbeitet das Video dann auf dem eigenen Server des Clubs. Es lässt sich abspielen,
sobald die erste Qualität (540p) bereit ist; die volle Qualität (1080p) folgt. Für ein
2-Stunden-Spiel dauert das insgesamt etwa eine halbe Stunde (gemessen: 540p in 9 Minuten,
1080p in 23 Minuten). Die Originaldateien behalten, bis das Video als *Bereit* angezeigt wird.

**Volleyball: Ballwechsel automatisch gefunden.** Danach sucht WiePlay die Ballwechsel selbst
(am Ball, an der Bewegung auf dem Feld und an den Pfiffen) und schlägt sie auf der
Tagging-Seite unter *Vorschläge prüfen* vor. Jeden Vorschlag annehmen, korrigieren oder
ablehnen, dann die Ballwechsel als geprüft markieren: WiePlay lernt aus jedem Spiel, das ein
Coach geprüft hat.

## 6. Was immer gilt

- WiePlay-Videos sind fürs Team. **Nie veröffentlichen**, keine Bildschirmaufnahmen
  ausserhalb von WiePlay teilen.
- Löschwünsche (von Spielerinnen, Gegnern, allen, die gefilmt wurden): einem Coach oder
  einem WiePlay-Admin melden; es wird gekürzt oder gelöscht, oder die Person wird aus den Tags
  entfernt.
- Ist trotzdem jemand unter 18 im Bild (Ballkind, Besuch auf dem Feld): vor dem Hochladen
  einem WiePlay-Admin melden.

## Checkliste

- [ ] Es ist ein Spiel, und WiePlay zeigt *Filmen erlaubt* dafür (andere Spiele: Prüfung beim Hochladen)
- [ ] Kamera hinter der Grundlinie (Volleyball) oder auf Höhe der Mittellinie (Basketball), hoch, fest
- [ ] Ganzes Feld und die hinteren Ecken im Bild, fester Zoom, kein Fischauge
- [ ] 1080p oder 4K mit **50 Bildern/s**, 1/500 s, Belichtung/Fokus/Weissabgleich fest, Ton an
- [ ] Akku und Karte geprüft; ab dem Einspielen filmen, keine Pausen
- [ ] In WiePlay hochgeladen (oder in die NAS-Inbox kopiert oder über einen Link importiert); Originale behalten bis *Bereit*
