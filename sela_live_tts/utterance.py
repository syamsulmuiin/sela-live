"""Pure natural_v1 utterance planning; never loads a model or renders audio."""
from dataclasses import dataclass
import math
import re

from voice_app.delivery import _text, load_registry
from voice_app.automatic_conditioning import PROFILE_IDS, load_router

_PREDICTION_BUDGET = 64
_LEGACY_TARGET = 180
_CLAUSE = re.compile(r'''[,;:]["'”’»)\]}]*\s+''')


_ABBREVIATIONS = frozenset(('dr', 'prof', 'dll', 'dsb', 'mr', 'mrs', 'ms', 'sr', 'jr', 'no', 'e.g', 'i.e'))
_BOUNDARY = re.compile(r'''[.!?]+["'”’»)\]}]*\s+''')
_PARAGRAPH = re.compile(r'\n\s*\n+')  # Exact pinned SDK paragraph separator.
_CODE = re.compile(r'(`+).*?\1', re.DOTALL)
_QUOTED = re.compile(r'''“[^”]*”|"[^"]*"|‘[^’]*’''', re.DOTALL)
_UNITS = re.compile(_CODE.pattern + '|' + _QUOTED.pattern + r'|Rp\s*\d[\d.,]*|\s+|\S+', re.DOTALL)


def _clause_cuts(text):
    protected = [(m.start(), m.end()) for pattern in (_CODE, _QUOTED)
                 for m in pattern.finditer(text)]
    return [m.end() for m in _CLAUSE.finditer(text)
            if not any(a <= m.start() < b for a, b in protected)]


def _sentences(text):
    """Conservative boundaries: ambiguous abbreviations/ellipses stay together."""
    protected = [(m.start(), m.end()) for m in _CODE.finditer(text)]
    cuts = []
    for match in _BOUNDARY.finditer(text):
        if any(a <= match.start() < b for a, b in protected):
            continue
        punctuation = re.match(r'[.!?]+', match.group()).group()
        # Closing quotation followed by lowercase attribution is not a new
        # sentence (language-agnostic conservative cue, not a names list).
        if (any(c in match.group()[len(punctuation):] for c in '\"\'”’»')
                and text[match.end():match.end() + 1].islower()):
            continue
        if punctuation.startswith('..'):
            continue
        if punctuation == '.':
            prefix = re.search(r'[\w.]+$', text[:match.start()])
            word = prefix.group().lower() if prefix else ''
            if word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha()):
                continue
        cuts.append(match.end())
    start = 0
    for end in (*cuts, len(text)):
        if end > start:
            yield start, end
        start = end


def _fits(raw, native):
    return (len(raw) <= 800 and len(raw.encode('utf-8')) <= 3200
            and len(native) <= 800 and len(native.encode('utf-8')) <= 3200)


@dataclass(frozen=True)
class SpeechRequest:
    text: str
    delivery: object = None


@dataclass(frozen=True)
class Prediction:
    duration_seconds: float | None
    model_token_count: int


@dataclass(frozen=True)
class ResolvedDelivery:
    profile_id: str = 'conditioning_b'
    intended_emotion: str = 'neutral'
    intensity: str = 'medium'


@dataclass(frozen=True)
class UtteranceGroup:
    start: int
    end: int
    source_text: str
    native_text: str
    reason: str
    prediction: Prediction | None
    fallback_reasons: tuple[str, ...] = ()

    @property
    def resource_envelope_verified(self):
        return (self.prediction is not None
                and self.prediction.duration_seconds is not None
                and self.prediction.duration_seconds <= 28
                and self.prediction.model_token_count <= 512)


@dataclass(frozen=True)
class UtterancePlan:
    source_text: str
    groups: tuple[UtteranceGroup, ...]
    delivery: ResolvedDelivery
    prediction_calls: int
    policy_id: str = 'natural_v1'
    independent_emotion_rendering_proven: bool = False


class NaturalSpeechPlanner:
    def __init__(self, *, normalize, predict, resolve_delivery=None):
        self._normalize = normalize
        self._predict = predict
        self._resolve_delivery = resolve_delivery

    def _delivery(self, selected):
        registry = load_registry()
        router = load_router(registry=registry)
        # Validate incoming frozen specs too: frozen is not trusted provenance.
        route = router.select(registry.resolve() if selected is None else selected)
        if self._resolve_delivery is None:
            profile, emotion, intensity = (route['profile_id'], route['intended_emotion'], route['intensity'])
            if type(profile) is not str or type(emotion) is not str or type(intensity) is not str:
                raise ValueError('Invalid delivery route')
            resolved = ResolvedDelivery(profile, emotion, intensity)
        else:
            resolved = self._resolve_delivery(selected)
        if (type(resolved) is not ResolvedDelivery
                or type(resolved.profile_id) is not str or resolved.profile_id not in PROFILE_IDS
                or type(resolved.intended_emotion) is not str or resolved.intended_emotion not in registry.ids
                or type(resolved.intensity) is not str or resolved.intensity not in ('low', 'medium', 'high')):
            raise ValueError('Invalid resolved delivery')
        return resolved

    async def plan(self, request):
        if type(request) is not SpeechRequest:
            raise ValueError('Expected SpeechRequest')
        text = _text(request.text)
        delivery = self._delivery(request.delivery)
        groups = []
        calls = 0
        normalized = {}
        predictions = {}

        async def estimate(native):
            nonlocal calls
            if native in predictions:
                return predictions[native]
            if calls >= _PREDICTION_BUDGET:
                return None
            calls += 1
            prediction = await self._predict(native, delivery.profile_id)
            if (type(prediction) is not Prediction
                    or type(prediction.model_token_count) is not int
                    or not 0 < prediction.model_token_count <= 32768):
                raise ValueError('Invalid model prediction')
            if prediction.duration_seconds is None:
                if prediction.model_token_count <= 512:
                    raise ValueError('Invalid model prediction')
            elif (type(prediction.duration_seconds) not in (int, float)
                    or not math.isfinite(prediction.duration_seconds)
                    or prediction.duration_seconds <= 0):
                raise ValueError('Invalid model prediction')
            predictions[native] = prediction
            return prediction

        def native_copy(start, end):
            key = (start, end)
            if key not in normalized:
                source = text[start:end].strip()
                native = self._normalize(source) if source else ''
                if source and (type(native) is not str or not native.strip()
                        or any((ord(c) < 32 and c not in '\n\t\r')
                               or 127 <= ord(c) <= 159 or 0xD800 <= ord(c) <= 0xDFFF
                               for c in native)):
                    raise ValueError('Invalid normalized text')
                normalized[key] = native
            return normalized[key]

        def append(start, end, reason, prediction, causes):
            groups.append(UtteranceGroup(start, end, text[start:end],
                native_copy(start, end), reason, prediction, tuple(dict.fromkeys(causes))))

        def bounded(start, end):
            # Greedy legacy-sized lexical/currency units; only an oversized unit
            # is subdivided. Both ACK source and normalized copies stay bounded.
            cursor = start
            pending = start
            for match in _UNITS.finditer(text, start, end):
                unit_end = match.end()
                native = native_copy(pending, unit_end)
                if _fits(text[pending:unit_end], native) and len(native) <= _LEGACY_TARGET:
                    cursor = unit_end
                    continue
                if cursor > pending:
                    yield pending, cursor
                    pending = cursor
                while pending < unit_end:
                    stop = min(unit_end, pending + _LEGACY_TARGET)
                    while stop > pending:
                        native = native_copy(pending, stop)
                        if _fits(text[pending:stop], native) and len(native) <= _LEGACY_TARGET:
                            break
                        stop -= 1
                    if stop == pending:
                        raise ValueError('Normalizer unit exceeds bounded limit')
                    if stop < unit_end:
                        yield pending, stop
                        pending = stop
                    else:
                        cursor = stop
                        break
            if pending < end:
                yield pending, end

        async def visit(start, end, reason, causes=()):
            nonlocal calls
            raw = text[start:end]
            native = native_copy(start, end)
            if calls >= _PREDICTION_BUDGET:
                for a, b in bounded(start, end):
                    append(a, b, 'bounded_fallback', None,
                           causes + ('prediction_budget_exhausted',))
                return
            rejected = []
            # SDK always separates paragraphs, regardless of max_chunk_length.
            # Respect those boundaries rather than admitting a context it resplits.
            if _PARAGRAPH.search(native):
                rejected.append('sdk_paragraph_boundary')
            if len(raw) > 800 or len(raw.encode('utf-8')) > 3200:
                rejected.append('raw_native_limit')
            if len(native) > 800 or len(native.encode('utf-8')) > 3200:
                rejected.append('normalized_native_limit')
            prediction = None
            if not rejected:
                if not native:
                    append(start, end, 'source_only', None, causes)
                    return
                prediction = await estimate(native)
                assert prediction is not None  # visit checked the per-request budget
                if prediction.duration_seconds is not None and prediction.duration_seconds > 28:
                    rejected.append('duration_limit')
                if prediction.model_token_count > 512:
                    rejected.append('model_token_limit')
                if not rejected:
                    append(start, end, reason, prediction, causes)
                    return
            causes = tuple(dict.fromkeys(causes + tuple(rejected)))
            paragraph_cuts = [m.end() for m in _PARAGRAPH.finditer(raw) if m.end() < len(raw)]
            points = [0, *paragraph_cuts, len(raw)]
            spans = list(zip(points, points[1:])) if paragraph_cuts else list(_sentences(raw))
            kind = 'complete_context' if paragraph_cuts else 'complete_sentence'
            if len(spans) == 1:
                cuts = _clause_cuts(raw)
                points = [0] + cuts + ([len(raw)] if not cuts or cuts[-1] != len(raw) else [])
                spans = list(zip(points, points[1:]))
                kind = 'clause_fallback'
                if len(spans) > 1:
                    # Search longest adjacent prefix first. No monotonicity of
                    # model duration/token count is assumed. Budget caps search.
                    index = 0
                    while index < len(spans):
                        if calls >= _PREDICTION_BUDGET:
                            await visit(start + spans[index][0], end, kind, causes)
                            return
                        a = start + spans[index][0]
                        accepted = False
                        for stop in range(len(spans), index, -1):
                            b = start + spans[stop - 1][1]
                            native = native_copy(a, b)
                            if not native or not _fits(text[a:b], native):
                                continue
                            candidate = await estimate(native)
                            if candidate is None:
                                break
                            if (candidate.duration_seconds is not None
                                    and candidate.duration_seconds <= 28
                                    and candidate.model_token_count <= 512):
                                append(a, b, kind, candidate, causes)
                                index = stop
                                accepted = True
                                break
                        if not accepted:
                            await visit(a, start + spans[index][1], kind, causes)
                            index += 1
                    return
            if len(spans) == 1:
                spans = [(a - start, b - start) for a, b in bounded(start, end)]
                kind = 'bounded_fallback'
            if len(spans) == 1:
                # A short over-duration piece still needs decomposition. Prefer
                # a balanced lexical boundary, retaining currency reading hints.
                units = list(_UNITS.finditer(raw))
                cuts = [m.end() for m in units if m.end() < len(raw)]
                if cuts:
                    cut = min(cuts, key=lambda n: abs(n - len(raw) / 2))
                    spans = [(0, cut), (cut, len(raw))]
                elif len(raw) > 1:
                    cut = len(raw) // 2
                    spans = [(0, cut), (cut, len(raw))]
                else:
                    # Never pretend an impossible singleton satisfies the model
                    # envelope. Host must keep its existing native guard active.
                    append(start, end, 'indivisible_resource_limit', prediction, causes)
                    return
            for a, b in spans:
                await visit(start + a, start + b, kind, causes)

        await visit(0, len(text), 'complete_context')
        return UtterancePlan(text, tuple(groups), delivery, calls)
