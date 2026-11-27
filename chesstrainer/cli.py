"""Command line interface:  python -m chesstrainer <command>"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import analyze, db, fetch, report


def _con(args):
    return db.connect(args.db)


def _engine_or_die(args, con) -> str:
    path = analyze.find_engine(args.engine, con)
    if not path:
        sys.exit("Stockfish nicht gefunden. Installiere es (z.B. `brew install stockfish`, "
                 "`sudo apt install stockfish` oder von https://stockfishchess.org/download/) und gib den "
                 "Pfad mit --engine an oder setze STOCKFISH_PATH.")
    db.set_setting(con, "engine", path)
    return path


def cmd_sync(args):
    con = _con(args)
    user = args.user or db.get_setting(con, "user")
    if not user:
        sys.exit("Bitte --user DEIN_CHESSCOM_NAME angeben.")
    db.set_setting(con, "user", user)
    db.set_setting(con, "classes", args.classes)
    print(f"Lade Partien von {user} ...")
    n = fetch.sync_user(con, user, months=args.months, classes=tuple(args.classes.split(",")),
                        max_games=args.max_games)
    print(f"{n} neue Partien gespeichert.")


def cmd_import(args):
    con = _con(args)
    db.set_setting(con, "user", args.user)
    n = fetch.import_pgn(con, Path(args.file).read_text(encoding="utf-8", errors="replace"), args.user)
    print(f"{n} Partien importiert.")


def cmd_analyze(args):
    con = _con(args)
    eng = _engine_or_die(args, con)
    workers = args.workers or max(1, (os.cpu_count() or 2) - 1)
    db.set_setting(con, "depth", str(args.depth))
    print(f"Analysiere mit {eng} (Tiefe {args.depth}, {workers} Prozesse) ...")
    n = analyze.run_analysis(con, eng, depth=args.depth, workers=workers, limit=args.limit)
    print(f"{n} Partien analysiert.")
    if analyze.missing_lines(con):
        print("Ergänze Varianten für ältere Puzzles ...")
        analyze.refresh(con, eng, depth=args.depth, workers=workers)


def cmd_refresh(args):
    con = _con(args)
    eng = _engine_or_die(args, con)
    workers = args.workers or max(1, (os.cpu_count() or 2) - 1)
    print(f"Berechne Varianten und Fehlertypen (Tiefe {args.depth}, {workers} Prozesse) ...")
    n = analyze.refresh(con, eng, depth=args.depth, workers=workers, force=args.force)
    print(f"{n} Puzzles ergänzt.")


def cmd_update(args):
    cmd_sync(args)
    cmd_analyze(args)


def cmd_report(args):
    r = report.build(_con(args))
    if r["moves"] == 0:
        sys.exit("Noch keine analysierten Partien.")
    print(f"{r['games']} Partien, {r['moves']} eigene Züge, {r['mistakes']} Fehler, {r['blunders']} Patzer "
          f"({r['rate']:.1f} pro 100 Züge)\n")
    for line in r["insights"]:
        print(" •", line)


def _serve_app(app, args):
    try:
        from waitress import serve as waitress_serve      # sturdier than Flask's built-in server
    except ImportError:
        app.run(host=args.host, port=args.port, debug=False, threaded=True)
    else:
        waitress_serve(app, host=args.host, port=args.port, threads=8)


def cmd_serve(args):
    from .web.app import create_app
    multi = args.multi or os.environ.get("CHESSTRAINER_MULTI") in ("1", "true", "yes")
    if multi:
        from .users import MultiConfig
        cfg = MultiConfig(data_dir=args.data_dir or os.environ.get("CHESSTRAINER_DATA_DIR") or "data",
                          depth=args.depth or 12, workers=args.workers or min(2, os.cpu_count() or 1),
                          months=args.months, max_games=args.max_games, max_users=args.max_users,
                          retention_days=args.retention_days, cooldown_min=args.cooldown,
                          contact=args.contact or os.environ.get("CHESSTRAINER_CONTACT", ""))
        if not os.environ.get("CHESSTRAINER_CONTACT") and cfg.contact:
            os.environ["CHESSTRAINER_CONTACT"] = cfg.contact        # goes into the User-Agent for Chess.com
        if not analyze.find_engine():
            print("WARNUNG: Stockfish nicht gefunden - Analysen schlagen fehl (siehe README / STOCKFISH_PATH).")
        app = create_app(multi=cfg)
        print(f"Mehrspieler-Modus: Daten in {Path(cfg.data_dir).resolve() / 'users'}, Tiefe {cfg.depth}, "
              f"{cfg.workers} Engine-Prozess(e), höchstens {cfg.max_users} Spieler.")
        if not os.environ.get("CHESSTRAINER_CONTACT"):
            print("Hinweis: Setze --contact oder CHESSTRAINER_CONTACT (E-Mail/URL). Sie steht in der Datenschutzseite "
                  "und im User-Agent für die Chess.com-API.")
        print(f"Öffne http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}")
        _serve_app(app, args)
        return
    password = args.password or os.environ.get("CHESSTRAINER_PASSWORD") or None
    env_user = os.environ.get("CHESSTRAINER_USER")
    if env_user:                                   # lets a container start without a separate setup step
        db.set_setting(_con(args), "user", env_user)
    app = create_app(args.db, depth=args.depth or 14, workers=args.workers, password=password,
                     auto_sync=args.auto_sync)
    local = args.host in ("127.0.0.1", "localhost", "::1")
    if not local and not password:
        print("WARNUNG: Der Server ist im Netzwerk erreichbar, aber ohne Passwort. "
              "Setze --password oder die Umgebungsvariable CHESSTRAINER_PASSWORD.")
    if args.auto_sync:
        print(f"Automatischer Abruf alle {args.auto_sync} Minuten.")
    print(f"Öffne http://{'127.0.0.1' if args.host == '0.0.0.0' else args.host}:{args.port}")
    _serve_app(app, args)


def main(argv=None):
    p = argparse.ArgumentParser(prog="chesstrainer", description=__doc__)
    p.add_argument("--db", default=None, help="Pfad zur SQLite-Datei (Standard: data/trainer.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_sync(sp):
        sp.add_argument("--user", help="Chess.com-Benutzername")
        sp.add_argument("--months", type=int, default=3, help="Wie viele Monatsarchive (Standard 3)")
        sp.add_argument("--classes", default="rapid,blitz", help="Zeitkategorien, z.B. rapid,blitz,bullet")
        sp.add_argument("--max-games", type=int, default=300)

    def add_an(sp):
        sp.add_argument("--engine", help="Pfad zur Stockfish-Binärdatei")
        sp.add_argument("--depth", type=int, default=14, help="Analysetiefe (Standard 14)")
        sp.add_argument("--workers", type=int, default=0, help="Parallele Engine-Prozesse (Standard: CPUs-1)")
        sp.add_argument("--limit", type=int, default=None, help="Nur die neuesten N Partien analysieren")

    s = sub.add_parser("sync", help="Partien von Chess.com laden"); add_sync(s); s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("import-pgn", help="PGN-Datei importieren")
    s.add_argument("file"); s.add_argument("--user", required=True); s.set_defaults(fn=cmd_import)
    s = sub.add_parser("analyze", help="Neue Partien mit Stockfish analysieren"); add_an(s); s.set_defaults(fn=cmd_analyze)
    s = sub.add_parser("update", help="sync + analyze"); add_sync(s); add_an(s); s.set_defaults(fn=cmd_update)
    s = sub.add_parser("refresh", help="Varianten ergänzen und Fehlertypen neu bestimmen (ohne Neu-Analyse)")
    add_an(s); s.add_argument("--force", action="store_true", help="auch vorhandene Varianten neu berechnen")
    s.set_defaults(fn=cmd_refresh)
    s = sub.add_parser("report", help="Muster-Zusammenfassung im Terminal"); s.set_defaults(fn=cmd_report)
    s = sub.add_parser("serve", help="Web-UI starten")
    s.add_argument("--host", default="127.0.0.1"); s.add_argument("--port", type=int, default=5000)
    s.add_argument("--depth", type=int, default=None, help="Analysetiefe (Standard 14, im Mehrspieler-Modus 12)")
    s.add_argument("--workers", type=int, default=0)
    s.add_argument("--multi", action="store_true",
                   help="Öffentlicher Mehrspieler-Modus: jeder gibt seinen Chess.com-Namen ein (eigene Datenbank pro Spieler)")
    s.add_argument("--data-dir", default=None, help="Mehrspieler: Ordner für die Spielerdaten (Standard data/)")
    s.add_argument("--max-games", type=int, default=40, help="Mehrspieler: neueste Partien pro Abruf (Standard 40)")
    s.add_argument("--months", type=int, default=2, help="Mehrspieler: Monatsarchive pro Abruf (Standard 2)")
    s.add_argument("--max-users", type=int, default=300, help="Mehrspieler: höchstens so viele Spieler (Standard 300)")
    s.add_argument("--retention-days", type=int, default=90,
                   help="Mehrspieler: Spieler ohne Besuch nach N Tagen löschen (0 = nie, Standard 90)")
    s.add_argument("--cooldown", type=int, default=15, help="Mehrspieler: Minuten zwischen zwei Abrufen eines Spielers")
    s.add_argument("--contact", default=None, help="Mehrspieler: Kontakt (E-Mail oder URL) für Datenschutzseite und User-Agent")
    s.add_argument("--password", help="Login-Passwort (oder Umgebungsvariable CHESSTRAINER_PASSWORD)")
    s.add_argument("--auto-sync", type=int, default=0, metavar="MINUTEN",
                   help="Neue Partien automatisch alle N Minuten laden und analysieren")
    s.set_defaults(fn=cmd_serve)
    args = p.parse_args(argv)
    args.fn(args)
