# Clarified emotional/prosodic delivery target

## Confirmed by user

The target is one consistent speaker identity with emotional/prosodic delivery applied to arbitrary dynamically generated utterances. It is not a library of prerecorded openings or fixed example sentences. Plutchik's eight supplied categories are the INITIAL review set, NOT a permanent maximum: the user explicitly intends to add more emotional categories over time.

## Design requirements, not a claim of implementation

- Keep speaker identity separate from spoken text and delivery metadata.
- Keep delivery/control metadata out of both displayed speech text and synthesized lexical content; do not simply hide inline tags from the UI.
- Represent the initial eight categories with stable IDs in an extensible, versioned registry. New validated categories must not require redesigning the conversation or ACK protocol.
- Validate category IDs and supported intensity/delivery parameters. Extensibility does not mean passing arbitrary free-form tags into a model or accepting unbounded control payloads.
- Map a requested delivery to demonstrated adapter/engine capabilities. Unsupported categories must be reported honestly; adding a label or changing speed alone is not proof of emotional expression.
- Preserve the selected speaker identity as a requirement when testing new emotional deliveries; do not silently switch voices or providers. Do not infer that a model's voice_style is only speaker identity, or that it guarantees independent eight-emotion control.
- Evaluate each new category with context-appropriate, varied utterances and human listening. Keep lexical fidelity, naturalness, speaker consistency, resource safety and subjective emotional suitability as separate gates.
- Preserve remote API/Codex LLM, consent/privacy, authentication, final-only dispatch, cancellation, exact source/ACK spans, bounded chunked long replies and native safety guards.

## Current gap

The mistaken approved-empathy opening has now been removed from production. The separate structured metadata boundary, extensible registry and disabled-by-default experimental numeric prosody adapter are installed and verified; see EXTENSIBLE-DELIVERY.md. Learned emotion-conditioned rendering with consistent M1 identity is still unproven. Previous sample approvals are reference performances, not permission to replay their literal wording in arbitrary replies. Installing the metadata boundary does not establish that emotional control is achieved.
