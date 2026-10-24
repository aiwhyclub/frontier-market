"""Optional real-AI draft adapter (EPIC 12, TRD §10).

Off unless FM_AI_ENABLED=1 and ANTHROPIC_API_KEY is set; each feature (F10.1~F10.6)
must also be listed in FM_AI_FEATURES (default: all six). A result is always an
unadopted draft: callers keep the rule-based path and never auto-apply it.

attempt() returns (text_or_None, run) where run is safe to store in ai_runs-style
fields: provider, model, started/finished, ok, error, usage, input_hash. It never
contains the API key or the full request text.
"""

import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-haiku-5-5"
ALL_FEATURES = ("F10.1", "F10.2", "F10.3", "F10.4", "F10.5", "F10.6")
TIMEOUT_SECONDS = 30


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def model():
    return os.environ.get("FM_AI_MODEL") or DEFAULT_MODEL


def adopted(feature_id):
    """True when this feature may make a real provider call."""
    if os.environ.get("FM_AI_ENABLED") != "1" or not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    listed = os.environ.get("FM_AI_FEATURES")
    features = [f.strip() for f in listed.split(",")] if listed else ALL_FEATURES
    return feature_id in features


def status(feature_id):
    """Shape shown to the UI: whether a real call would happen and with which model."""
    return {"feature": feature_id, "adopted": adopted(feature_id),
            "model": model() if adopted(feature_id) else None}


def attempt(feature_id, system, prompt, max_tokens=1200):
    """Call the provider once. Returns (text, run); text is None when not adopted or failed."""
    input_hash = "sha256:" + hashlib.sha256((system + "\n" + prompt).encode()).hexdigest()
    if not adopted(feature_id):
        return None, {"feature": feature_id, "attempted": False, "mode": "rule_based",
                      "model": None, "input_hash": input_hash, "reason": "not_adopted"}
    run = {"feature": feature_id, "attempted": True, "provider": "anthropic", "model": model(),
           "started_at": _now(), "input_hash": input_hash, "ok": False, "usage": None, "error": None}
    body = json.dumps({"model": model(), "max_tokens": max_tokens, "system": system,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read())
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        run.update(ok=bool(text), usage=data.get("usage"), model=data.get("model", run["model"]),
                   error=None if text else "empty_response")
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
        text = ""
        run["error"] = f"{type(exc).__name__}: {getattr(exc, 'code', '') or exc}"[:200]
    run["finished_at"] = _now()
    run["mode"] = "ai" if run["ok"] else "rule_based"
    return (text or None), run
