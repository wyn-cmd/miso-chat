# DeepSeek price estimates for the admin stats page.
#
# Rates are per one million tokens, split the way DeepSeek bills: input that hits the prompt cache,
# input that misses it, and output. DeepSeek bills the completion time of each call in one of two
# tiers: peak hours (01:00-04:00 and 06:00-10:00 UTC, Monday to Friday) at the peak rates, and every
# other hour at the off-peak rates, which are half of the peak ones. The store records, for each
# DeepSeek call, which tier its completion fell in, so the estimate follows the hours the tokens
# were actually used. The rates are the published ones for DeepSeek's chat and reasoner models,
# mapped onto the two DeepSeek ids this server offers (deepseek-flash and deepseek-v4-pro). That
# mapping makes the result an estimate, not a quote: check DeepSeek's pricing page for your
# account, and change the table here or override it with the deepseek_prices setting (JSON in the
# same shape) if your plan differs. Chinese public holidays are treated as ordinary weekdays, which
# can overstate the cost on those days.
#
# CNY rates are the ones DeepSeek publishes for CNY, not a conversion of the USD ones, so the two
# currencies can differ slightly at the same token counts.

import datetime
import json

CURRENCIES = ("usd", "cny")

DEFAULT_PRICES = {
    "deepseek-flash": {
        "usd": {
            "peak": {"cache_hit": 0.006, "cache_miss": 0.30, "output": 1.20},
            "offpeak": {"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.60},
        },
        "cny": {
            "peak": {"cache_hit": 0.04, "cache_miss": 2.00, "output": 8.00},
            "offpeak": {"cache_hit": 0.02, "cache_miss": 1.00, "output": 4.00},
        },
    },
    "deepseek-v4-pro": {
        "usd": {
            "peak": {"cache_hit": 0.044, "cache_miss": 1.32, "output": 3.96},
            "offpeak": {"cache_hit": 0.022, "cache_miss": 0.66, "output": 1.98},
        },
        "cny": {
            "peak": {"cache_hit": 0.30, "cache_miss": 9.00, "output": 27.00},
            "offpeak": {"cache_hit": 0.15, "cache_miss": 4.50, "output": 13.50},
        },
    },
}

# The rates an unknown DeepSeek id is priced at, so a new model still shows a figure. Said plainly in
# the payload so the page can mark it.
FALLBACK_MODEL = "deepseek-flash"

NOTE = ("Estimates in {currency}, at published DeepSeek rates per million tokens. Peak hours "
        "(01:00-04:00 and 06:00-10:00 UTC, Monday to Friday) bill at the peak rate and every other "
        "hour at the off-peak rate, half of it. Chinese public holidays count as weekdays, so calls "
        "then can be priced high, and any plan of your own is not included.")

# The peak windows in UTC, as (start, end) times: from the start up to, not including, the end.
PEAK_WINDOWS = ((datetime.time(1, 0), datetime.time(4, 0)),
                (datetime.time(6, 0), datetime.time(10, 0)))


def is_offpeak(dt):
    # Which tier a call's completion time falls in. DeepSeek bills by the completion timestamp:
    # peak on weekdays inside the two UTC windows, off-peak otherwise (weekends included). A naive
    # time is read as UTC.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    utc = dt.astimezone(datetime.timezone.utc)
    if utc.weekday() >= 5:
        return True
    clock = utc.time()
    for start, end in PEAK_WINDOWS:
        if start <= clock < end:
            return False
    return True


def _clean_rates(raw):
    # Keeps only the three known rates, as non-negative numbers, so bad JSON cannot break the page.
    out = {}
    for key in ("cache_hit", "cache_miss", "output"):
        try:
            value = float(raw.get(key))
        except (TypeError, ValueError, AttributeError):
            return None
        if value < 0:
            return None
        out[key] = value
    return out


def _clean_tiers(raw):
    # A currency block as the two tiers. A block in the older single-tier shape is accepted as the
    # peak tier, with the off-peak tier taken as half of it.
    if not isinstance(raw, dict):
        return None
    if "peak" in raw or "offpeak" in raw:
        peak = _clean_rates(raw.get("peak"))
        offpeak = _clean_rates(raw.get("offpeak"))
        if peak is None or offpeak is None:
            return None
        return {"peak": peak, "offpeak": offpeak}
    flat = _clean_rates(raw)
    if flat is None:
        return None
    return {"peak": flat,
            "offpeak": {key: value / 2.0 for key, value in flat.items()}}


def clean(price):
    # A per-model entry with both currencies, dropping anything malformed.
    if not isinstance(price, dict):
        return None
    out = {}
    for currency in CURRENCIES:
        tiers = _clean_tiers(price.get(currency))
        if tiers is not None:
            out[currency] = tiers
    return out or None


def load(store):
    # The defaults, with any admin override from the deepseek_prices setting merged over them, per
    # currency, so an override that sets only USD keeps the default CNY rates.
    prices = {name: _two_tier(price) for name, price in DEFAULT_PRICES.items()}
    flash = _two_tier(DEFAULT_PRICES[FALLBACK_MODEL])
    raw = store.get_setting("deepseek_prices") if store is not None else ""
    if raw:
        try:
            override = json.loads(raw)
        except (TypeError, ValueError):
            override = None
        if isinstance(override, dict):
            for name, price in override.items():
                fixed = clean(price)
                if fixed:
                    merged = dict(flash)
                    merged.update(prices.get(str(name), {}))
                    merged.update(fixed)
                    prices[str(name)] = merged
    return prices


def _two_tier(price):
    # A defaults row, which is already in the two-tier shape, copied for safety.
    return {currency: {"peak": dict(tiers["peak"]), "offpeak": dict(tiers["offpeak"])}
            for currency, tiers in price.items()}


def bare_model(model):
    # The engine names a provider model 'deepseek:deepseek-flash'; the price table uses the bare id.
    text = str(model or "")
    return text.split(":", 1)[1] if text.startswith("deepseek:") else text


def rates_for(prices, model):
    # The rates and whether they came from the model's own row or the fallback.
    own = prices.get(bare_model(model))
    if own:
        return own, False
    return prices.get(FALLBACK_MODEL) or DEFAULT_PRICES[FALLBACK_MODEL], True


def row_cost(tokens, rates):
    # Cost of one row: the peak share of each bucket at the peak rates, the rest at the off-peak
    # rates. Rows from before the split was recorded carry no peak figures and price as off-peak.
    input_tokens = int(tokens.get("input_tokens") or 0)
    cached = min(int(tokens.get("cached_tokens") or 0), input_tokens)
    uncached = max(0, input_tokens - cached)
    output = int(tokens.get("output_tokens") or 0)
    peak_cached = min(int(tokens.get("peak_cached_tokens") or 0), cached)
    peak_miss = min(int(tokens.get("peak_miss_tokens") or 0), uncached)
    peak_output = min(int(tokens.get("peak_output_tokens") or 0), output)
    peak, offpeak = rates["peak"], rates["offpeak"]
    return (peak_cached * peak["cache_hit"] + peak_miss * peak["cache_miss"]
            + peak_output * peak["output"]
            + (cached - peak_cached) * offpeak["cache_hit"]
            + (uncached - peak_miss) * offpeak["cache_miss"]
            + (output - peak_output) * offpeak["output"]) / 1_000_000.0


def summary(rows, prices):
    # Per-model figures and costs in both currencies, plus totals for each currency. Each model's
    # peak tokens (cache-hit input, cache-miss input and output billed at the peak rate) are listed
    # beside the totals, so the split is visible.
    models = []
    totals = {currency: 0.0 for currency in CURRENCIES}
    token_totals = {"requests": 0, "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0,
                    "seconds": 0.0, "peak_tokens": 0}
    for row in rows:
        model = str(row.get("model") or "")
        rates, fallback = rates_for(prices, model)
        item = {
            "model": bare_model(model),
            "requests": int(row.get("requests") or 0),
            "input_tokens": int(row.get("input_tokens") or 0),
            "cached_tokens": int(row.get("cached_tokens") or 0),
            "output_tokens": int(row.get("output_tokens") or 0),
            "seconds": float(row.get("seconds") or 0.0),
            "fallback_rates": fallback,
            "peak_tokens": _peak_total(row),
            "cost": {},
        }
        for currency in CURRENCIES:
            cost = row_cost(row, rates[currency])
            item["cost"][currency] = round(cost, 6)
            totals[currency] += cost
        for key in token_totals:
            token_totals[key] += item[key]
        models.append(item)
    return {
        "models": models,
        "totals": dict(token_totals, cost={c: round(v, 6) for c, v in totals.items()}),
        "prices": prices,
        "note": NOTE,
    }


def _peak_total(row):
    # Tokens in one row billed at the peak rate: the three peak buckets, clamped to the row's totals
    # so bad data cannot push a figure past what the row recorded.
    input_tokens = int(row.get("input_tokens") or 0)
    cached = min(int(row.get("cached_tokens") or 0), input_tokens)
    uncached = max(0, input_tokens - cached)
    output = int(row.get("output_tokens") or 0)
    peak_cached = min(int(row.get("peak_cached_tokens") or 0), cached)
    peak_miss = min(int(row.get("peak_miss_tokens") or 0), uncached)
    peak_output = min(int(row.get("peak_output_tokens") or 0), output)
    return peak_cached + peak_miss + peak_output
