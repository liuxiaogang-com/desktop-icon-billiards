"""Update feed parsing and fetching.

The feed contract is documented in ``docs/UPDATE.md``. The top-level release is
authoritative; ``history`` is optional display-only data. The download link is
opened in the browser, never executed or written over the running executable.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from .config import INTERNAL_NAME

MAX_FEED_BYTES = 64 * 1024
MAX_NOTES = 4000
VERSION_PATTERN = re.compile(r'\d{1,6}\.\d{1,6}\.\d{1,6}')


def version_tuple(value):
    """Return ``(major, minor, patch)`` for a release version string."""
    if not isinstance(value, str) or not VERSION_PATTERN.fullmatch(value):
        raise ValueError('version must use major.minor.patch')
    return tuple(int(part) for part in value.split('.'))


def release_entries(info):
    """Return the display list: latest release first, then valid history entries."""
    latest = version_tuple(info['version'])
    entries = [{'version': info['version'], 'notes': info.get('notes', '')}]
    seen = {latest}
    history = info.get('history', [])
    if not isinstance(history, list):
        return entries
    for row in history[:100]:
        if not isinstance(row, dict):
            continue
        try:
            key = version_tuple(row.get('version'))
        except ValueError:
            continue
        notes = row.get('notes', '')
        if key >= latest or key in seen or not isinstance(notes, str) or len(notes) > MAX_NOTES:
            continue
        seen.add(key)
        entries.append({'version': row['version'], 'notes': notes})
    entries[1:] = sorted(entries[1:], key=lambda row: version_tuple(row['version']), reverse=True)
    return entries[:21]


def parse_update(data, current):
    """Parse a raw feed response. Returns a release dict, or None when up to date."""
    info = json.loads(data.decode('utf-8-sig'))
    if not isinstance(info, dict):
        raise ValueError('Update JSON must be an object')
    latest = version_tuple(info.get('version'))
    installed = version_tuple(current)
    if latest <= installed:
        return None
    url = info.get('download_url', '')
    notes = info.get('notes', '')
    if not isinstance(url, str):
        raise ValueError('Invalid download URL')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('download_url must be an HTTPS URL')
    if not isinstance(notes, str) or len(notes) > MAX_NOTES:
        raise ValueError('notes must be text, up to 4000 characters')
    result = {'version': info['version'], 'download_url': url, 'notes': notes}
    if 'history' in info:
        result['history'] = release_entries(info)[1:]
    return result


def download(url, limit, version):
    request = urllib.request.Request(
        url,
        headers={'User-Agent': f'DesktopIconBilliards/{version}', 'Cache-Control': 'no-cache'},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Response is too large')
    return data


def fetch_update(url, current, *, source='startup', allow_insecure=False):
    """Fetch and validate the feed. HTTP is accepted only for local tests."""
    if not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme
    if scheme != 'https' and not (allow_insecure and scheme == 'http'):
        raise ValueError('Update URL must use HTTPS')
    if source not in ('startup', 'manual'):
        raise ValueError('Update source must be startup or manual')
    query = [(key, value) for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
             if key not in ('app', 'version', 'source')]
    query.extend((('app', INTERNAL_NAME), ('version', current), ('source', source)))
    url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(query)))
    return parse_update(download(url, MAX_FEED_BYTES, current), current)
