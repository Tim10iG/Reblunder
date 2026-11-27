"""Translations: German source strings are the keys, catalogs exist for en, fr and es.

Three mechanisms share one catalog (``strings/``):
* Python code calls ``tr("Deutscher Text {n}", n=3)``; the language comes from a context variable
  that the web layer sets for each request (default: German).
* Jinja templates are translated *as source text* when they are loaded (``translate_source``), so
  templates keep their German text and need no markup.
* JavaScript receives the catalog for the current language as ``window.I18N`` and calls ``t(...)``.
"""
from __future__ import annotations

import contextvars
import re

from .strings import ALL

LANGS = ("de", "en", "fr", "es")
NAMES = {"de": "Deutsch", "en": "English", "fr": "Français", "es": "Español"}
DEFAULT = "en"

_lang = contextvars.ContextVar("lang", default="de")


def _build():
    cat = {l: {} for l in LANGS if l != "de"}
    for row in ALL:
        de = row[0]
        for l, txt in zip(("en", "fr", "es"), row[1:]):
            cat[l][de] = txt
    return cat


CATALOG = _build()


def set_lang(lang: str):
    return _lang.set(lang if lang in LANGS else DEFAULT)


def get_lang() -> str:
    return _lang.get()


def tr(text: str, **kw) -> str:
    """Translate a German source string and fill {placeholders}."""
    lang = _lang.get()
    out = text if lang == "de" else CATALOG[lang].get(text, text)
    return out.format(**kw) if kw else out


def pick(accept_language: str | None) -> str:
    """Best supported language from an Accept-Language header, else DEFAULT."""
    if not accept_language:
        return DEFAULT
    best, best_q = DEFAULT, -1.0
    for part in accept_language.split(","):
        bits = part.strip().split(";")
        code = bits[0].strip().lower()[:2]
        q = 1.0
        for b in bits[1:]:
            if b.strip().startswith("q="):
                try:
                    q = float(b.strip()[2:])
                except ValueError:
                    q = 0.0
        if code in LANGS and q > best_q:
            best, best_q = code, q
    return best


_rx_cache: dict[str, re.Pattern] = {}


def _regex(lang: str) -> re.Pattern:
    if lang not in _rx_cache:
        keys = sorted(CATALOG[lang], key=len, reverse=True)
        parts = []
        for k in keys:
            e = re.escape(k)
            if re.match(r"\w", k[0]):
                e = r"(?<!\w)" + e
            if re.match(r"\w", k[-1]):
                e += r"(?!\w)"
            parts.append(e)
        _rx_cache[lang] = re.compile("|".join(parts))
    return _rx_cache[lang]


def translate_source(src: str, lang: str) -> str:
    """Replace every known German fragment in template/JS source text by its translation."""
    if lang == "de":
        return src
    cat = CATALOG[lang]
    return _regex(lang).sub(lambda m: cat[m.group(0)], src)


def js_catalog(lang: str) -> dict:
    return {} if lang == "de" else CATALOG[lang]


class Msg:
    """A message that is translated when it is rendered (so worker threads can create it without a request)."""
    __slots__ = ("key", "kw")

    def __init__(self, key: str, **kw):
        self.key, self.kw = key, kw

    def __str__(self) -> str:
        return tr(self.key, **self.kw)

    __repr__ = __str__


_DATE = {"de": "%d.%m.%Y", "en": "%d %b %Y", "fr": "%d/%m/%Y", "es": "%d/%m/%Y"}


def fmt_date(ts) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(_DATE[_lang.get()])
