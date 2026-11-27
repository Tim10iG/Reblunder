# REBLUNDER

Aus deinen Patzern werden deine Puzzles. (Das Python-Paket und die Befehle heißen weiterhin `chesstrainer`.)

Lädt deine Chess.com-Partien, analysiert sie mit Stockfish, erkennt wiederkehrende Fehlermuster und
gibt dir vor jeder Partie **5 Puzzles aus deinen eigenen Fehlern** (mit Wiederholungslogik).
Alles läuft lokal, es gibt keinen Account und keine Cloud.

## Voraussetzungen

* Python 3.10+
* Stockfish (Binärdatei): `brew install stockfish` (macOS), `sudo apt install stockfish` (Linux)
  oder Download von <https://stockfishchess.org/download/> (Windows). Der Pfad kann mit `--engine`
  oder der Umgebungsvariable `STOCKFISH_PATH` angegeben werden. Liegt `stockfish*` im Projektordner,
  wird es automatisch gefunden.

```bash
pip install -r requirements.txt
```

## Schnellstart

```bash
# 1. Partien laden (letzte 3 Monate, Rapid + Blitz) und analysieren – einmalig, dauert ein paar Minuten
python -m chesstrainer update --user DEIN_CHESSCOM_NAME

# 2. Web-UI starten, dann http://127.0.0.1:5000 öffnen
python -m chesstrainer serve
```

* **Warm-up** (Startseite): 5 Puzzles, Brett per Klick bedienen. Pro Puzzle 3 Versuche. Danach siehst du,
  was in der Partie passiert ist, wie lange du für den Zug gebraucht hast und was du künftig prüfen solltest.
* **Muster-Report**: Fehlertypen, Fehlerquote nach Spielphase / Tempo / Stellung / Farbe, Entwicklung über
  je 10 Partien, Trainingsfortschritt.
* **Eröffnungen**: je Farbe, welche Eröffnungen du spielst, Punkteausbeute, Fehler in den ersten 10 Zügen,
  Gewinnchance nach Zug 10, wiederkehrende Fehlerstellungen (dieselbe Stellung mehrfach erreicht) und
  einzelne Eröffnungsfehler mit Brett. Über „Nur Eröffnung“ im Warm-up übst du nur diese Fehler.
* **Partieanalyse**: Liste deiner Partien; in jeder Partie blätterst du Zug für Zug, siehst den Verlauf deiner
  Gewinnchance, springst zu jedem Fehler und klickst die Varianten durch. Dazu Muster über alle Partien und der
  Export der Fehlerbibliothek als CSV oder PGN.
* **Handy**: Die Oberfläche passt sich an (Navigation unten, Brett in voller Breite, Pfeile per Antippen).
  Wie du sie unterwegs erreichst, steht in `DEPLOY.md`. `serve` kennt dafür `--host 0.0.0.0`, `--password`
  und `--auto-sync MINUTEN`.
* **Fortschritt**: deine letzten 25 Partien gegen die 25 davor (Fehlerquote, Patzer, Wertung, Punkte),
  Wertungsverlauf, welche Fehlertypen seltener oder häufiger werden, Eröffnungsstellungen, die du erneut
  falsch behandelst, und wie gut du wiederholte Puzzles löst.
* **Pfeile auf dem Brett**: Rechtsklick + Ziehen zeichnet einen Pfeil, Rechtsklick markiert ein Feld,
  Linksklick löscht alles. Umschalt/Strg = rot, Alt = blau, beides = gelb.
* **Beispiele im Report**: zu jedem Fehlertyp und jeder Situation die neuesten Stellungen mit deinem Zug (rot)
  und dem besseren Zug (grün), mit Link zur Partie.
* **Varianten nach dem Puzzle**: Nach jeder Aufgabe kannst du die beste Fortsetzung (Lösungszug + 3 Züge)
  und die Folge nach deinem Partiezug durchklicken (Züge anklicken, ◀ ▶ oder Pfeiltasten, „Abspielen“).
* **Kosten eines Fehlers**: überall steht der Verlust an Gewinnchance in Prozentpunkten und die
  Engine-Bewertung vorher → nachher in Bauerneinheiten (z.B. +0.4 → −1.9, „M3“ = Matt in 3).
* **Aktualisieren** (oben rechts): lädt neue Partien und analysiert sie im Hintergrund. Gleiches Ergebnis
  wie `python -m chesstrainer update`.

Nach einem Update des Programms: `python -m chesstrainer refresh` ergänzt Varianten und Bewertungen für schon
analysierte Partien und bestimmt die Fehlertypen neu (ohne die Partien neu zu analysieren).

Einzelne Schritte: `sync`, `analyze`, `refresh`, `report` (Text-Zusammenfassung im Terminal), `import-pgn DATEI --user NAME`
(z.B. für Lichess- oder Chess.com-PGN-Exporte). Mit `python -m chesstrainer <befehl> -h` siehst du alle Optionen.
Nützlich: `--months 6`, `--classes rapid,blitz,bullet`, `--depth 16`, `--workers 4`.

## So funktioniert es

1. **Download** über die öffentliche Chess.com-API (kein Login). Nur normale Partien (keine Varianten),
   standardmäßig Rapid und Blitz. Bereits geladene Partien werden nicht doppelt gespeichert.
2. **Analyse**: Stockfish bewertet jede Stellung einmal. Der Verlust eines Zuges ist der Abfall deiner
   Gewinnwahrscheinlichkeit (Lichess-Formel). **Fehler** ≥ 10 Prozentpunkte, **Patzer** ≥ 20. Stellungen, die schon
   entschieden sind (< 8 % oder > 92 %), zählen nicht. Aus der Uhr (`%clk`) wird die Bedenkzeit pro Zug berechnet.
3. **Puzzle-Erzeugung**: Für jeden Fehler wird die Stellung *vor* deinem Zug ein Puzzle. Gleichwertige
   Alternativzüge (bis 4 Prozentpunkte hinter dem besten) werden ebenfalls akzeptiert.
4. **Fehlertyp**: regelbasierte Erkennung (python-chess) anhand der Engine-Varianten, zum Beispiel
   *Figur eingestellt*, *Drohung übersehen*, *Gabel / Fesselung / Abzug zugelassen oder übersehen*,
   *mehrzügige Taktik*, *Bauer verloren*, *Matt zugelassen*. Fehler ohne Materialfolge werden nach Situation
   eingeteilt: *Eröffnungsprinzip verletzt*, *Vorteil nicht verwertet*, *Beste Verteidigung verpasst*,
   *Königsstellung geschwächt*, *Endspieltechnik*. Dazu Kontext: Phase, Farbe, Zeitnot, zu schneller Zug, Vorteil verspielt.
5. **Auswahl der 5 Puzzles**: fällige zuerst, bevorzugt aus deinen aktuell häufigsten Mustern, neuere Fehler und
   Puzzles, die du schon einmal nicht gelöst hast. Höchstens ein Puzzle pro Partie, Muster werden gemischt.
6. **Wiederholung** (Leitner-Boxen): im ersten Versuch richtig → nächste Box (Wiederkehr nach 1 / 3 / 7 / 21 Tagen).
   Falsch → zurück, kommt beim nächsten Warm-up wieder.

## Anpassen

Schwellenwerte, Tempo-Definition (`FAST_MOVE_SECS`) und alle deutschen Texte stehen in `chesstrainer/classify.py`.
Farben, Schriften, Abstände und Brett-Design stehen als Variablen ganz oben in `chesstrainer/web/static/style.css`.
Schriften (OFL) und Figuren (CC BY-SA 3.0) liegen lokal unter `static/fonts` und `static/pieces`, jeweils mit Lizenzhinweis.
Intervalle der Wiederholung in `chesstrainer/puzzles.py` (`BOX_HOURS`).

## Für Freunde (Mehrspieler)

Mit `python -m chesstrainer serve --multi` startet ein öffentlicher Modus: Jeder gibt auf der Startseite seinen Chess.com-Namen ein und bekommt eine eigene Analyse (eigene Datenbank pro Spieler, ohne Passwort). Die Analysen laufen nacheinander in einer Warteschlange, Spieler können ihre Daten selbst löschen. Aufbau, Limits und Hosting stehen in `DEPLOY.md` (Abschnitt D).

## Grenzen

* Die Fehlertyp-Erkennung ist eine Heuristik und liegt im Einzelfall daneben. Ein LLM-Schritt, der Stellung, gespielten Zug, Engine-Zug und Variante erklärt, wäre die
  naheliegende Erweiterung (`classify.motif_tags` ist der Ort dafür).
* Im Puzzle selbst ist ein Zug zu finden. Die Fortsetzung wird danach gezeigt, nicht abgefragt.
* Figuren werden angeklickt bzw. angetippt, nicht gezogen.
* Je tiefer `--depth`, desto genauer, aber langsamer. 14 ist für Mustererkennung ein guter Kompromiss.

## Tests und Demo

```bash
pip install pytest && python -m pytest tests
python tools/make_demo_games.py --engine /pfad/zu/stockfish --games 10 --out data/demo.pgn
python -m chesstrainer import-pgn data/demo.pgn --user demo_user
python -m chesstrainer analyze && python -m chesstrainer serve
```
