"""Test helper: find the t('key') call the UI uses for a Korean source text (static/i18n.js, ko table)."""
import json
import re
from pathlib import Path

I18N_JS = Path(__file__).resolve().parent.parent / "static" / "i18n.js"


def _tables():
    text = I18N_JS.read_text(encoding="utf-8")
    out = {}
    for lang in ("en", "ko"):
        block = re.search(r"\b%s: \{\n(.*?)\n  \}" % lang, text, re.S).group(1)
        table = {}
        for line in block.splitlines():
            m = re.match(r'\s*("(?:[^"\\]|\\.)*"): ("(?:[^"\\]|\\.)*"),?$', line)
            if m:
                table[json.loads(m.group(1))] = json.loads(m.group(2))
        out[lang] = table
    return out


TABLES = _tables()
_KO_TO_KEY = {}
for _key, _ko in TABLES["ko"].items():
    _KO_TO_KEY.setdefault(_ko, _key)


def key_for(korean):
    return _KO_TO_KEY[korean]


def tk(korean):
    """The source text of the UI call for `korean`, e.g. t('s2.record_type')."""
    return "t('%s')" % key_for(korean)
