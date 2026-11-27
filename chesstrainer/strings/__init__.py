"""Catalog rows: (de, en, fr, es). German is the key; placeholders stay identical in every language."""
from .ui import ROWS as _ui
from .pages import ROWS as _pages
from .reports import ROWS as _reports
from .drills import ROWS as _drills
from .motifs import ROWS as _motifs
from .opening_names import ROWS as _opening_names
from .training import ROWS as _training

ALL = [*_ui, *_pages, *_reports, *_drills, *_motifs, *_opening_names, *_training]
