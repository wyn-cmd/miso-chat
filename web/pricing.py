# DeepSeek price estimates for the admin stats page.
#
# Rates are per one million tokens, split the way DeepSeek bills: input that hits the prompt cache,
# input that misses it, and output. They are the published standard rates for DeepSeek's chat and
# reasoner models, mapped onto the two DeepSeek ids this server offers (deepseek-flash and
# deepseek-v4-pro). That mapping makes the result an estimate, not a quote: check DeepSeek's pricing
# page for your account, and change the table here or override it with the deepseek_prices setting
# (JSON in the same shape) if your plan differs. Off-peak discounts are not modelled.
#
# CNY rates are the ones DeepSeek publishes for CNY, not a conversion of the USD ones, so the two
# currencies can differ slightly at the same token counts.

import json

CURRENCIES = ("usd", "cny")

DEFAULT_PRICES = {
    "deepseek-flash": {
        "usd": {"cache_hit": 0.07, "cache_miss": 0.27, "output": 1.10},
        "cny": {"cache_hit": 0.50, "cache_miss": 2.00, "output": 8.00},
    },
    "deepseek-v4-pro": {
        "usd": {"cache_hit": 0.14, "cache_miss": 0.55, "output": 2.19},
        "cny": {"cache_hit": 1.00, "cache_miss": 4.00, "output": 16.00},
    },
}

# The rates an unknown DeepSeek id is priced at, so a new model still shows a figure. Said plainly in
# the payload so the page can mark it.
FALLBACK_MODEL = "deepseek-flash"

NOTE = ("Estimates in {currency}, at published DeepSeek rates per million tokens. Cached input is billed at the "
        "cache-hit rate and the rest of the input at the miss rate. Off-peak discounts and any plan of your own "
        "are not included.")


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


def clean(price):
    # A per-model entry with both currencies, dropping anything malformed.
    if not isinstance(price, dict):
        return None
    out = {}
    for currency in CURRENCIES:
        rates = _clean_rates(price.get(currency))
        if rates is not None:
            out[currency] = rates
    return out or None


def load(store):
    # The defaults, with any admin override from the deepseek_prices setting merged over them.
    prices = {name: clean(price) or dict(DEFAULT_PRICES[FALLBACK_MODEL]) for name, price in DEFAULT_PRICES.items()}
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
                    prices[str(name)] = fixed
    return prices


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
    # Cost of one row: cached input at the hit rate, the rest of the input at the miss rate, output separate.
    input_tokens = int(tokens.get("input_tokens") or 0)
    cached = min(int(tokens.get("cached_tokens") or 0), input_tokens)
    uncached = max(0, input_tokens - cached)
    output = int(tokens.get("output_tokens") or 0)
    return (cached * rates["cache_hit"] + uncached * rates["cache_miss"] + output * rates["output"]) / 1_000_000.0


def summary(rows, prices):
    # Per-model figures and costs in both currencies, plus totals for each currency.
    models = []
    totals = {currency: 0.0 for currency in CURRENCIES}
    token_totals = {"requests": 0, "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "seconds": 0.0}
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
            "cost": {},
        }
        for currency in CURRENCIES:
            cost = row_cost(item, rates[currency])
            item["cost"][currency] = round(cost, 6)
            totals[currency] += cost
        for key in token_totals:
            token_totals[key] += item[key]
        models.append(item)
    return {
        "models": models,
        "totals": dict(token_totals, cost={c: round(v, 6) for c, v in totals.items()}),
        "prices": prices,
    }
