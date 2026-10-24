"""Swappable fake for backend.ai.attempt used by the F10.4~F10.6 tests.

`install(case, payload)` patches `backend.ai.attempt` with a fake that returns `payload` (dict -> JSON
text; str -> raw text; None -> failure). With SUPERLITE_REAL=1 nothing is patched, so the real adapter
(not adopted without a key) runs and tests that need draft content are skipped.
"""

import json
import os
from unittest import mock

from backend import ai

REAL = os.environ.get("SUPERLITE_REAL") == "1"


def install(case, payload, model="claude-test-1"):
    calls = []
    if REAL:
        return calls

    def fake(feature, system, prompt, max_tokens=1200):
        calls.append({"feature": feature, "system": system, "prompt": prompt})
        base = {"feature": feature, "attempted": True, "provider": "anthropic", "model": model,
                "started_at": "2026-10-24T00:00:00Z", "finished_at": "2026-10-24T00:00:01Z",
                "input_hash": "sha256:test", "usage": {"input_tokens": 10, "output_tokens": 20}}
        if payload is None:
            return None, {**base, "ok": False, "error": "URLError: boom", "mode": "rule_based"}
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return text, {**base, "ok": True, "error": None, "mode": "ai"}

    patcher = mock.patch.object(ai, "attempt", fake)
    patcher.start()
    case.addCleanup(patcher.stop)
    return calls
