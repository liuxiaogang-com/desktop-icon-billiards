"""Reader for GNU gettext ``.po`` catalogs.

The catalogs are ordinary ``.po`` files so translators can use Poedit, Weblate or Crowdin
without touching Python. Because no build machine here has ``msgfmt``, the files are read
directly; a ``.po`` file is a small text format and parsing it costs one dependency-free
pass over a few hundred entries.

Chinese is the source language: ``msgid`` holds the Chinese text, and ``msgstr`` the
translation. An entry with an empty ``msgstr`` therefore falls back to Chinese on its own,
which is exactly gettext's "untranslated means show the source" rule.

Each entry also carries ``msgctxt``, the stable key the code looks up (``t('panel.toolbar
.quit')``). Using the key as context keeps two things working that msgid alone would break:
a string shared by several keys can still be translated differently later, and a key stays
greppable even after its Chinese wording is edited.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

CONTEXT_PREFIX = 'C-'  # gettext's convention for "the context is a stable id, not a word"


class PoError(ValueError):
    """Raised for a catalog that cannot be read."""


class UnsupportedPoFeature(PoError):
    """Raised for valid gettext features this reader deliberately does not implement."""


_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\', 'a': '\a', 'b': '\b',
            'f': '\f', 'v': '\v'}
_SIMPLE = {'n', 't', 'r', '"', '\\', 'a', 'b', 'f', 'v'}


def _unescape(text):
    """Decode the escape sequences a quoted .po string may contain."""
    out, index = [], 0
    while index < len(text):
        char = text[index]
        if char != '\\':
            out.append(char)
            index += 1
            continue
        index += 1
        if index >= len(text):
            raise PoError('string ends with a trailing backslash')
        code = text[index]
        if code in _SIMPLE:
            out.append(_ESCAPES[code])
            index += 1
        elif code in '01234567':
            digits = text[index:index + 3]
            if len(digits) < 3 or not all(digit in '01234567' for digit in digits):
                raise PoError(f'bad octal escape: \\{digits}')
            out.append(chr(int(digits, 8)))
            index += 3
        elif code == 'x':
            digits = text[index + 1:index + 3]
            if len(digits) != 2:
                raise PoError(f'bad hex escape: \\x{digits}')
            out.append(chr(int(digits, 16)))
            index += 3
        else:
            raise PoError(f'unknown escape: \\{code}')
    return ''.join(out)


def _quoted(text):
    """The content of one ``"..."`` literal, unescaped."""
    body = text.strip()
    if len(body) < 2 or body[0] != '"' or body[-1] != '"':
        raise PoError(f'expected a quoted string, got {body!r}')
    return _unescape(body[1:-1])


def _is_continuation(line):
    return line.startswith('"')


def _tokens(lines):
    """Yield ``(keyword, value, line_number)`` from the lines of one entry.

    A value split over several quotes is joined into one string, so a multi-line
    ``msgid`` arrives as the single text it represents. Continuation lines may be
    indented for readability, which gettext tools also emit.
    """
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        number = index + 1
        index += 1
        if line.startswith('#'):
            continue
        if not line:
            continue
        head, _, rest = line.partition(' ')
        if head in ('msgctxt', 'msgid', 'msgid_plural', 'msgstr') or head.startswith('msgstr['):
            parts = [_quoted(rest)]
            while index < len(lines) and _is_continuation(lines[index].strip()):
                parts.append(_quoted(lines[index]))
                index += 1
            yield head, ''.join(parts), number
        else:
            raise PoError(f'line {number}: unexpected {line!r}')


@dataclass
class Catalog:
    """One language: its code, its own name, and the ``key -> text`` it provides."""

    language: str
    name: str
    messages: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)  # key -> the Chinese text it translates


def header_values(entry):
    """Parse the ``Key: value`` lines of a .po header entry."""
    values = {}
    for line in entry.splitlines():
        key, separator, value = line.partition(':')
        if separator:
            values[key.strip().lower()] = value.strip()
    return values


def load(path):
    """Read one ``.po`` file into a :class:`Catalog`."""
    path = Path(path)
    try:
        text = path.read_text(encoding='utf-8-sig')
    except OSError as error:
        raise PoError(f'{path.name}: {error}') from error
    blocks, current = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            # A comment-only block is not an entry, so comments never split or create one;
            # obsolete entries (#~) are superseded and must not be read at all.
            if line.startswith('#~'):
                continue
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(current)

    messages, sources = {}, {}
    language = name = ''
    for number, block in enumerate(blocks):
        entry = {'msgctxt': None, 'msgid': None, 'msgstr': None, 'plural': False}
        for keyword, value, _ in _tokens(block):
            if keyword == 'msgid_plural':
                entry['plural'] = True
            elif keyword.startswith('msgstr['):
                if entry['msgstr'] is None:
                    entry['msgstr'] = value  # nplurals[0] is the singular form
            else:
                entry[keyword] = value
        if entry['msgid'] is None:
            raise PoError(f'{path.name}: entry {number + 1} has no msgid')
        if entry['msgstr'] is None:
            raise PoError(f'{path.name}: entry {number + 1} has no msgstr')
        if entry['plural']:
            raise UnsupportedPoFeature(f'{path.name}: plurals are not supported ({entry["msgid"]!r})')
        if entry['msgid'] == '':
            if not entry['msgstr']:
                raise PoError(f'{path.name}: entry {number + 1} has an empty header')
            header = header_values(entry['msgstr'])
            language = header.get('language', '')
            name = header.get('x-language-name', '')
            continue
        key = entry['msgctxt'] or entry['msgid']
        messages[key] = entry['msgstr'] or entry['msgid']  # untranslated falls back to Chinese
        sources[key] = entry['msgid']
    if not language:
        raise PoError(f'{path.name}: the header has no Language field')
    return Catalog(language=language, name=name or language, messages=messages, sources=sources)


def dump(catalog, path, *, project='Desktop Icon Billiards', version='', comment=''):
    """Write ``catalog`` back out as a .po file, so a test can prove the files round-trip."""
    lines = ['# ' + comment if comment else '# Desktop Icon Billiards message catalog',
             '# This file is read by dib/locales/po.py; edit it with Poedit or any .po tool.',
             'msgid ""',
             'msgstr ""',
             f'"Project-Id-Version: {project} {version}\\n"',
             f'"Language: {catalog.language}\\n"',
             f'"X-Language-Name: {catalog.name}\\n"',
             '"MIME-Version: 1.0\\n"',
             '"Content-Type: text/plain; charset=UTF-8\\n"',
             '"Content-Transfer-Encoding: 8bit\\n"',
             '']
    for key, msgstr in catalog.messages.items():
        source = catalog.sources.get(key, '')
        lines.append(f'#: {key}')
        lines.extend(_entry_lines('msgctxt', key))
        lines.extend(_entry_lines('msgid', source))
        lines.extend(_entry_lines('msgstr', msgstr))
        lines.append('')
    Path(path).write_text('\n'.join(lines), encoding='utf-8')


def _quote(text):
    escaped = (text.replace('\\', '\\\\').replace('"', '\\"').replace('\t', '\\t')
               .replace('\r', '\\r').replace('\n', '\\n'))
    return f'"{escaped}"'


def _entry_lines(keyword, value):
    """One keyword, wrapped over continuation lines on newlines so diffs stay readable."""
    if '\n' not in value:
        return [f'{keyword} {_quote(value)}']
    parts = value.split('\n')
    pieces = [part + '\n' for part in parts[:-1]]
    if parts[-1]:
        pieces.append(parts[-1])
    return [f'{keyword} ""'] + [f'    {_quote(piece)}' for piece in pieces]
