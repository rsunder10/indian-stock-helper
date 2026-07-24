"""Parse a model's JSON text into an AnalystVerdict, defensively.

Local/cloud models don't always return clean JSON — they may wrap it in prose or a
```json fence. This extracts the first balanced JSON object and validates it.
"""

from __future__ import annotations

import json

from indi_analyst.llm.base import ProviderError
from indi_analyst.models import AnalystVerdict


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # strip a ```json ... ``` fence
        text = text.split("```", 2)[1]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    # find the first balanced {...}
    start = text.find("{")
    if start == -1:
        raise ProviderError("No JSON object found in model response.")
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ProviderError("Unbalanced JSON in model response.")


def parse_verdict(text: str) -> AnalystVerdict:
    try:
        raw = json.loads(_extract_json(text))
    except json.JSONDecodeError as e:
        raise ProviderError(f"Model returned invalid JSON: {e}") from e
    try:
        return AnalystVerdict.model_validate(raw)
    except Exception as e:  # pydantic ValidationError and friends
        raise ProviderError(f"Model JSON did not match the verdict schema: {e}") from e
