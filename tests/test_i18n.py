import re
from pathlib import Path

import pytest

from chesstrainer import classify as C, i18n, repertoire
from chesstrainer.i18n import CATALOG, LANGS, tr
from chesstrainer.strings import ALL

ROOT = Path(__file__).resolve().parent.parent / "chesstrainer"


def test_rows_complete_and_unique():
    seen = {}
    for row in ALL:
        assert len(row) == 4 and all(isinstance(x, str) and x.strip() for x in row), row[0]
        assert row[0] not in seen or seen[row[0]] == row[1:], f"duplicate key with different text: {row[0]!r}"
        seen[row[0]] = row[1:]


def test_placeholders_match():
    rx = re.compile(r"\{\{.*?\}\}|\{[a-z_]+\}")
    for row in ALL:
        base = sorted(rx.findall(row[0]))
        for lang, txt in zip(("en", "fr", "es"), row[1:]):
            assert sorted(rx.findall(txt)) == base, (lang, row[0])


def test_no_quote_breaking_in_translations():
    for row in ALL:                       # translations are pasted into inline scripts, so no straight apostrophes
        for txt in row[1:]:
            assert "'" not in re.sub(r"\{\{.*?\}\}", "", txt) and '"' not in txt, txt


def test_motifs_contexts_and_drills_are_translated():
    for t, (title, hint) in C._MOTIFS.items():
        for lang in ("en", "fr", "es"):
            assert title in CATALOG[lang] and hint in CATALOG[lang], t
    for c, label in C._CONTEXTS.items():
        assert label in CATALOG["en"], c
    for d in repertoire.DRILLS:
        for key in (d["group"], d["title"], d["hint"]):
            for lang in ("en", "fr", "es"):
                assert key in CATALOG[lang], key[:40]


def test_js_keys_have_translations():
    from chesstrainer.web.app import _js_keys
    keys = _js_keys()
    assert keys
    for k in keys:
        assert k in CATALOG["en"], f"t({k!r}) has no catalog entry"


def test_tr_and_msg():
    i18n.set_lang("de")
    tok = i18n.set_lang("en")
    try:
        assert tr("Weiter") == "Next"
        assert str(i18n.Msg("Analysiere Partie {d} von {t} …", d=2, t=9)) == "Analysing game 2 of 9 …"
        assert tr("Unbekannter Text") == "Unbekannter Text"
    finally:
        i18n._lang.reset(tok)
    assert tr("Weiter") == "Weiter"


def test_pick():
    assert i18n.pick("fr-CH,fr;q=0.9,en;q=0.8") == "fr"
    assert i18n.pick("pt-BR,pt;q=0.9") == "en"
    assert i18n.pick("de-DE,de;q=0.9,en;q=0.5") == "de"
    assert i18n.pick(None) == "en"
