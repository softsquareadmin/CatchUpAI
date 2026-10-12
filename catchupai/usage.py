"""Persist API token usage and calculate USD costs at a versioned price list."""

import copy
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


PRICE_DATE = "2026-10-12"
PRICE_SOURCE = "https://developers.openai.com/api/docs/pricing"
LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "api_usage.jsonl"
_FILE_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)

# USD per million tokens. Keep old log records' rates when updating this table.
PRICES = {
    "gpt-realtime-2.1": {
        "text_input": "4", "audio_input": "32", "image_input": "5",
        "text_cached": "0.40", "audio_cached": "0.40", "image_cached": "0.50",
        "text_output": "24", "audio_output": "64",
    },
    "gpt-5.4-mini": {"input": "0.75", "cached": "0.075", "output": "4.50"},
    "gpt-5.4-mini-2026-03-17": {"input": "0.75", "cached": "0.075", "output": "4.50"},
}


def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError("Missing or invalid token count")
    return value


def calculate_cost(model, usage, api="realtime"):
    """Return a Decimal cost, or None when usage/rates cannot establish it."""
    rates = PRICES.get(model)
    if rates is None or not isinstance(usage, dict):
        return None
    try:
        inputs = _count(usage.get("input_tokens"))
        outputs = _count(usage.get("output_tokens"))
        charges = []
        if api == "realtime":
            details = usage.get("input_token_details") or {}
            cached_total = _count(details.get("cached_tokens", 0 if inputs == 0 else None))
            cached = details.get("cached_tokens_details") or {}
            input_sum = cached_sum = 0
            for modality in ("text", "audio", "image"):
                # Audio/text details are required when there is input; image is optional.
                tokens = _count(details.get(f"{modality}_tokens", 0 if modality == "image" or inputs == 0 else None))
                cache = _count(cached.get(f"{modality}_tokens", 0))
                if cache > tokens:
                    return None
                input_sum += tokens
                cached_sum += cache
                charges.extend(((tokens - cache, rates[f"{modality}_input"]),
                                (cache, rates[f"{modality}_cached"])))
            if input_sum != inputs or cached_sum != cached_total:
                return None
            audio_output = _count((usage.get("output_token_details") or {}).get("audio_tokens", 0))
            if audio_output > outputs:
                return None
            # The output total already includes reasoning. Never add it a second time.
            charges.extend(((outputs - audio_output, rates["text_output"]),
                            (audio_output, rates["audio_output"])))
        elif api == "responses":
            cached = _count((usage.get("input_tokens_details") or {}).get("cached_tokens"))
            if cached > inputs:
                return None
            charges.extend(((inputs - cached, rates["input"]),
                            (cached, rates["cached"]), (outputs, rates["output"])))
        else:
            return None
        return sum((Decimal(tokens) * Decimal(rate) for tokens, rate in charges), Decimal(0)) / Decimal(1_000_000)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


class UsageTracker:
    """One logical session, retained across browser Realtime reconnections."""

    def __init__(self, session_id=None, log_path=LOG_PATH):
        self.session_id = session_id or uuid.uuid4().hex
        self.log_path = Path(log_path)
        self.total_cost = Decimal(0)
        self.unpriced_responses = 0
        self.usage_gaps = 0
        self.response_ids = set()
        self.lock = threading.Lock()

    def _write(self, record):
        # Logging must not break audio capture or report delivery.
        try:
            with _FILE_LOCK:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=True) + "\n")
        except OSError:
            _LOGGER.warning("Could not persist API usage to %s", self.log_path, exc_info=True)

    def _base(self, event):
        return {"event": event, "session_id": self.session_id,
                "logged_at": datetime.now(timezone.utc).isoformat()}

    def _totals(self):
        return {"observed_cost_usd": format(self.total_cost, "f"),
                "responses": len(self.response_ids),
                "unpriced_responses": self.unpriced_responses,
                "usage_gaps": self.usage_gaps,
                "complete": self.unpriced_responses == 0 and self.usage_gaps == 0}

    def record(self, response_id, model, usage, purpose=None, status=None, api="realtime"):
        if not response_id:
            self.mark_gap("response_without_id")
            return None
        with self.lock:
            key = (api, response_id)
            if key in self.response_ids:
                return None
            self.response_ids.add(key)
            cost = calculate_cost(model, usage, api)
            if cost is None:
                self.unpriced_responses += 1
            else:
                self.total_cost += cost
            record = {
                **self._base("api_response"), "api": api, "model": model,
                "response_id": response_id, "purpose": purpose, "status": status,
                "usage": copy.deepcopy(usage),
                "cost_usd": format(cost, "f") if cost is not None else None,
                "pricing_date": PRICE_DATE, "pricing_source": PRICE_SOURCE,
                "rates_usd_per_million": copy.deepcopy(PRICES.get(model)),
                "session_totals": self._totals(),
            }
            self._write(record)
            print("[API USAGE] " + json.dumps(record))
            return record

    def mark_gap(self, reason):
        with self.lock:
            self.usage_gaps += 1
            self._write({**self._base("usage_gap"), "reason": reason})

    def log_summary(self):
        with self.lock:
            record = {**self._base("usage_summary"), **self._totals()}
            self._write(record)
            print("[API USAGE TOTAL] " + json.dumps(record))
            return record
