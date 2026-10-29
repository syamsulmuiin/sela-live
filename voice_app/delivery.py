"""App-owned intended delivery metadata, not proven emotion conditioning.

The registry selects modest experimental pacing/pause presets only. Labels never
enter native synthesis or conversation history; source text is never rewritten.
"""
from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from types import MappingProxyType

SUPPORT_LEVEL = 'prosody_only_experimental'
MAX_TEXT_CHARS = 4000
MAX_ENVELOPE_CHARS = 65536  # Includes 12-character surrogate-pair escapes per source character.
MAX_REGISTRY_BYTES = 32768
_ID = re.compile(r'[a-z][a-z0-9_]{0,31}\Z')
_SCALES = {'low': 0.5, 'medium': 1.0, 'high': 1.5}


@dataclass(frozen=True)
class DeliverySpec:
    emotion: str
    intensity: str
    speed: float
    silence_duration: float
    registry_version: int = 1
    support_level: str = SUPPORT_LEVEL

    def native_controls(self) -> dict:
        return {'speed': float(self.speed), 'silence_duration': float(self.silence_duration)}


@dataclass(frozen=True)
class GeneratedReply:
    text: str
    delivery: DeliverySpec


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate delivery field')
        result[key] = value
    return result


def _json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid number')))
    except (ValueError, TypeError, RecursionError):
        raise ValueError('Invalid delivery JSON') from None


def _number(value, lower, upper):
    return type(value) in (int, float) and lower <= value <= upper and math.isfinite(value)


@dataclass(frozen=True)
class DeliveryRegistry:
    version: int
    entries: object

    @property
    def ids(self):
        return tuple(self.entries)

    def resolve(self, emotion='neutral', intensity='medium'):
        if (not isinstance(emotion, str) or emotion not in self.entries
                or not isinstance(intensity, str) or intensity not in _SCALES):
            raise ValueError('Unknown delivery selection')
        entry = self.entries[emotion]
        scale = _SCALES[intensity]
        return DeliverySpec(emotion, intensity,
                            round(1.05 + (entry['speed'] - 1.05) * scale, 6),
                            round(0.3 + (entry['silence_duration'] - 0.3) * scale, 6),
                            self.version)

    def instruction(self):
        return (' Return exactly one JSON object with ONLY fields "text" (the literal reply) '
                'and "delivery" (an object with ONLY "emotion" and "intensity"). '
                'No Markdown fences, commentary, tags, or extra fields. '
                'Choose emotion from ' + ', '.join(self.ids) + '; intensity must be '
                'low, medium, or high. Delivery is intended presentation, not detection '
                'of user emotion; these labels select experimental prosody only, not '
                'proven emotional conditioning. Never put delivery metadata in text. '
                'Preserve code, URLs, functions, quoted text and identifiers exactly '
                'inside text using JSON escaping. Keep text nonempty, at most 4000 '
                'characters and at most three sentences. Choose neutral when unsure.')

    def capabilities(self):
        return {'protocol_version': 1, 'native_emotion_conditioning': False,
                'native_controls': ['speed', 'silence_duration'],
                'speaker_identity_unchanged': True, 'registry_version': self.version,
                'emotion_ids': list(self.ids), 'support_level': SUPPORT_LEVEL}


def load_registry(path=None):
    path = Path(path) if path is not None else Path(__file__).with_name('delivery_registry.json')
    with path.open('rb') as handle:
        raw = handle.read(MAX_REGISTRY_BYTES + 1)
    if len(raw) > MAX_REGISTRY_BYTES:
        raise ValueError('Delivery registry size limit')
    data = _json(raw)
    if (type(data) is not dict or set(data) != {'version', 'entries'}
            or type(data['version']) is not int or not 1 <= data['version'] <= 2147483647
            or type(data['entries']) is not dict or not 1 <= len(data['entries']) <= 64
            or 'neutral' not in data['entries']):
        raise ValueError('Invalid delivery registry')
    entries = {}
    for identifier, entry in data['entries'].items():
        if (not _ID.fullmatch(identifier) or type(entry) is not dict
                or set(entry) != {'speed', 'silence_duration', 'support_level'}
                or entry['support_level'] != SUPPORT_LEVEL
                or not _number(entry['speed'], 0.95, 1.08)
                or not _number(entry['silence_duration'], 0.24, 0.36)):
            raise ValueError('Invalid delivery registry entry')
        entries[identifier] = MappingProxyType(dict(entry))
    if entries['neutral']['speed'] != 1.05 or entries['neutral']['silence_duration'] != 0.3:
        raise ValueError('Neutral delivery must be unchanged')
    return DeliveryRegistry(data['version'], MappingProxyType(entries))


def _text(text):
    if (not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS
            or any((ord(c) < 32 and c not in '\n\t\r') or 127 <= ord(c) <= 159
                   or 0xD800 <= ord(c) <= 0xDFFF for c in text)):
        raise ValueError('Invalid reply text')
    return text


def parse_reply(raw, registry=None):
    registry = registry if registry is not None else load_registry()
    if not isinstance(raw, str) or len(raw) > MAX_ENVELOPE_CHARS:
        raise ValueError('Delivery envelope size limit')
    data = _json(raw)
    if type(data) is not dict or set(data) != {'text', 'delivery'}:
        raise ValueError('Invalid delivery envelope')
    metadata = data['delivery']
    if type(metadata) is not dict or set(metadata) != {'emotion', 'intensity'}:
        raise ValueError('Invalid delivery metadata')
    return GeneratedReply(_text(data['text']), registry.resolve(**metadata))


def plain_reply(text, registry):
    return GeneratedReply(_text(text), registry.resolve())
