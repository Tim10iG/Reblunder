# Auf dem Handy nutzen

Die App ist eine Webseite. Das Handy öffnet sie im Browser; über „Zum Home-Bildschirm“ verhält sie sich wie eine App.
Es gibt drei Wege, von schnell bis unabhängig vom Laptop.

## A. Laptop läuft, Handy greift zu (5 Minuten)

```powershell
$env:CHESSTRAINER_PASSWORD = "ein-langes-passwort"
python -m chesstrainer serve --host 0.0.0.0 --auto-sync 10
```

* Im selben WLAN: `http://<IP des Laptops>:5000` (IP mit `ipconfig`, Windows-Firewall beim ersten Start erlauben).
* Von unterwegs: [Tailscale](https://tailscale.com) auf Laptop und Handy installieren und am Handy
  `http://<Tailscale-Name des Laptops>:5000` öffnen. Es muss kein Port am Router geöffnet werden.
* `--auto-sync 10` lädt und analysiert alle 10 Minuten neue Partien. „Aktualisieren“ in der App stößt das sofort an.

## B. Kleiner Rechner zu Hause (Raspberry Pi, Mini-PC, NAS)

Braucht Docker. Die Docker-Dateien sind geschrieben, aber von mir nicht auf einem echten Gerät getestet.

```bash
# Projektordner auf das Gerät kopieren, dann:
nano docker-compose.yml          # Chess.com-Namen und Passwort eintragen
mkdir -p data                    # optional: vorhandene data/trainer.db vom Laptop hierher kopieren
docker compose up -d --build
docker compose logs -f           # zeigt den ersten Abruf und die Analyse
```

Danach wie bei A per WLAN-Adresse oder Tailscale öffnen. Die erste Analyse dauert auf einem Raspberry Pi
deutlich länger als auf dem Laptop; kopierst du `data/trainer.db` mit, entfällt sie und dein Trainingsstand bleibt.

## C. Mietserver im Internet

Wie B, zusätzlich **HTTPS davor schalten**, sonst wandert das Passwort unverschlüsselt durchs Netz.
Am einfachsten mit [Caddy](https://caddyserver.com) und einer eigenen (Sub-)Domain:

```
schach.example.de {
    reverse_proxy localhost:5000
}
```

In `docker-compose.yml` dann `"127.0.0.1:5000:5000"` eintragen, damit nur Caddy den Port erreicht.
Ein Server mit 1–2 CPU-Kernen reicht; die Analyse einer Partie dauert dort eher eine halbe Minute als Sekunden.

## Gut zu wissen

* **Passwort:** Ohne `CHESSTRAINER_PASSWORD` (oder `--password`) ist die App für jeden im Netz offen. Die Anmeldung
  gilt 60 Tage pro Gerät.
* **Nach einer Partie:** Chess.com stellt neue Partien über seine öffentliche Schnittstelle meist nach wenigen Minuten
  bereit, garantiert ist das nicht. Tippe auf „Aktualisieren“; die Partie erscheint unter Partieanalyse ganz oben.
* **Ein Stand für alle Geräte:** Die Datenbank liegt auf dem Server. Laptop und Handy sehen dieselben Puzzles
  und denselben Trainingsfortschritt, solange beide dieselbe Adresse öffnen.
* **Pfeile am Handy:** „Pfeile zeichnen“ unter dem Brett einschalten, dann Startfeld und Zielfeld antippen.


## D. Öffentlich für Freunde (Mehrspieler-Modus)

Hier gibt jeder auf der Startseite seinen Chess.com-Namen ein und bekommt eine eigene Analyse. Es gibt kein Passwort; jeder Spieler hat eine eigene Datenbank (`data/users/<name>.db`).

**Was du brauchst**

- Einen kleinen Linux-Server mit Docker, z. B. Hetzner CX22 (2 vCPU, 4 GB RAM, ca. 4–5 € im Monat). Azure geht auch, ist aber für diesen Zweck unnötig teuer.
- Eine Domain (ca. 1 € im Monat), die per A-Record auf die Server-IP zeigt. Ohne https lässt sich die App am Handy nicht installieren.
- Stockfish ist im Docker-Image schon enthalten (`apt install stockfish`), du lädst nichts hoch.

**Schritte**

1. Projektordner auf den Server kopieren (z. B. `git clone` oder `scp -r`).
2. In `Caddyfile` die Domain eintragen, in `docker-compose.public.yml` bei `CHESSTRAINER_CONTACT` deine E-Mail-Adresse.
3. Starten:
   ```bash
   docker compose -f docker-compose.public.yml up -d --build
   ```
4. `https://deine-domain` öffnen und einmal mit dem eigenen Namen testen. Mit `https://deine-domain/health` siehst du, wie viele Spieler angemeldet sind und wie viele in der Warteschlange stehen.

**Wie der Betrieb begrenzt wird** (alles per `serve`-Option einstellbar)

| Option | Standard | Bedeutung |
|---|---|---|
| `--max-games` | 40 | neueste Partien, die pro Abruf analysiert werden |
| `--depth` | 12 | Analysetiefe (im Einzelbetrieb 14) |
| `--workers` | 2 | Engine-Prozesse für die gerade laufende Analyse |
| `--max-users` | 300 | höchstens so viele Spieler insgesamt |
| `--cooldown` | 15 | Minuten zwischen zwei Abrufen desselben Spielers |
| `--retention-days` | 90 | Spieler ohne Besuch werden danach gelöscht |

Es läuft immer nur eine Analyse gleichzeitig, alle anderen warten in einer Schlange und sehen ihren Platz. Pro IP-Adresse können pro Stunde höchstens 6 neue Spieler angelegt werden.

**Vor dem Teilen bedenken**

- Die Datenschutzseite (`/privacy`) ist ein Entwurf und keine Rechtsberatung. In Deutschland kann für öffentlich erreichbare Seiten ein Impressum nötig sein; bei Unsicherheit lass es prüfen.
- Wer deinen Chess.com-Namen kennt, kann dein Konto öffnen und die Analyse sehen. Das sind nur Auswertungen öffentlicher Partien, und jeder kann seine Daten selbst löschen.
- Stockfish steht unter GPL-3.0; die Datenschutzseite verlinkt die Quelle.
- Sichere `data/` mit einem Backup. Dort liegen alle Spielerdaten und der Schlüssel für die Cookies (`data/secret_key`).
