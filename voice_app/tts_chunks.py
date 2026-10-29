"""Bounded source partitions; never accumulate audio or rewrite history text."""
import re

_TAG = r'<(?:laugh|breath|sigh)>'
_UNITS = re.compile(_TAG + r'|Rp\s*\d[\d.,]*|\s+|[^\s<>]+')


def synthesis_source(part):
    """Keep ACK metadata separate from the native, whitespace-bounded copy.

    Compact only oversized source; splitting a currency's internal whitespace
    would lose its reading hint. Ordinary source reaches normalization unchanged.
    """
    source = part.strip()
    if len(source) > 800 or len(source.encode('utf-8')) > 3200:
        source = re.sub(r'\s+', ' ', source)
    return source


def source_parts(text, normalize, target=180):
    """Preserve every source character, with atomic tags and reading hints.

    The target applies to the pronunciation copy, not just the original. Long
    whitespace is carried by the neighbouring spoken piece, not synthesized as
    an empty request. Only a single oversized lexical unit needs subdivision.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError('TTS text must contain 1–4000 characters')
    text.encode('utf-8')  # Fail before inference on malformed Unicode.
    if any(ord(c) < 32 and c not in '\n\t' for c in text):
        raise ValueError('text_control')
    without_tags = re.sub(_TAG, '', text)
    if '<' in without_tags or '>' in without_tags:
        raise ValueError('unsupported_tag')

    def fits(part):
        source = synthesis_source(part)
        spoken = normalize(source)
        return (len(source) <= 800 and len(source.encode('utf-8')) <= 3200
                and len(spoken) <= target and len(spoken.encode('utf-8')) <= target * 4
                and len(re.findall(_TAG, part)) <= 3)

    units = []
    for match in _UNITS.finditer(text):
        unit = match.group()
        if unit.isspace() or fits(unit):
            units.append(unit)
        else:
            # Source-preserving fallback for unbroken oversized words. Tags
            # cannot reach this branch, so no allowed control can be bisected.
            while unit:
                end = min(target, len(unit))
                while end and not fits(unit[:end]):
                    end -= 1
                if not end:
                    raise ValueError('Pronunciation unit exceeds bounded limit')
                units.append(unit[:end]); unit = unit[end:]
    parts, pending = [], ''
    for unit in units:
        if pending.strip() and (not fits(pending + unit) or
                (len(normalize(pending.strip())) >= target // 2 and re.search(r'[.!?]\s+$', pending))):
            if unit.isspace():
                pending += unit
                continue
            parts.append(pending); pending = ''
        pending += unit
    if pending.strip():
        parts.append(pending)
    elif pending:
        parts[-1] += pending
    return parts
