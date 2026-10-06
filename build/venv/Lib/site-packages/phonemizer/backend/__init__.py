# Copyright 2015-2021 Mathieu Bernard
#
# This file is part of phonologizer: you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# Phonologizer is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with phonologizer. If not, see <http://www.gnu.org/licenses/>.
"""Multilingual text to phonemes converter"""

# pylint: disable=unused-import

from .espeak.espeak import EspeakBackend
from .espeak.mbrola import EspeakMbrolaBackend
from .festival.festival import FestivalBackend

# The segments backend is imported lazily: it depends on the 'segments'
# package, which in turn pulls in a heavy dependency chain (csvw, rdflib,
# jsonschema, babel, language-tags). Users of the espeak or festival
# backends do not need any of it. The import below therefore happens on
# first access rather than at module import time, so that phonemizer
# remains usable when 'segments' is not installed.

__all__ = [
    'EspeakBackend', 'EspeakMbrolaBackend', 'FestivalBackend',
    'SegmentsBackend', 'BACKENDS']


def _import_segments_backend():
    """Import and return SegmentsBackend, raising a helpful error if missing"""
    try:
        from .segments import SegmentsBackend
    except ImportError as err:  # pragma: nocover
        raise ImportError(
            'the segments backend requires the "segments" package, which '
            'is not installed. Install it with "pip install segments" or '
            '"pip install phonemizer[segments]".') from err
    return SegmentsBackend


def __getattr__(name):
    """Lazy access to SegmentsBackend (PEP 562)

    Allows "from phonemizer.backend import SegmentsBackend" to keep working
    without importing the segments package when it is not needed.

    """
    if name == 'SegmentsBackend':
        return _import_segments_backend()
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


class _Backends(dict):
    """A mapping of backend names to classes, resolving 'segments' lazily

    Behaves as a regular dict for the espeak and festival backends. The
    'segments' entry is only imported when it is actually looked up, so
    that BACKENDS['espeak'] does not require the segments package.

    """
    _LAZY = {'segments': _import_segments_backend}

    def __missing__(self, key):
        if key in self._LAZY:
            value = self._LAZY[key]()
            self[key] = value
            return value
        raise KeyError(key)

    def _all_keys(self):
        keys = list(dict.__iter__(self))
        keys.extend(k for k in self._LAZY if not dict.__contains__(self, k))
        return keys

    def __iter__(self):
        return iter(self._all_keys())

    def keys(self):
        return self._all_keys()

    def values(self):
        return [self[k] for k in self._all_keys()]

    def items(self):
        return [(k, self[k]) for k in self._all_keys()]

    def __contains__(self, key):
        return dict.__contains__(self, key) or key in self._LAZY

    def __len__(self):
        return len(self._all_keys())


BACKENDS = _Backends(
    {b.name(): b for b in (
        EspeakBackend, FestivalBackend, EspeakMbrolaBackend)})
"""The different phonemization backends as a mapping (name, class)"""
