"""Configurable limits and cost snapshots. Amounts are estimated JPY."""
from decimal import Decimal
import json
import os

LIMITS = {
    'summary': (10, 30000, 4000), 'chat': (60, 12000, 1500),
    'quiz': (10, 20000, 4000), 'grading': (60, 5000, 1000),
}


def settings():
    # Standard text pricing verified 2026-10-14; cached inputs conservatively use full price.
    prices = json.loads(os.getenv('AI_MODEL_PRICES', '{"gpt-5.4-mini":[0.75,4.5],"gpt-5.4-nano":[0.2,1.25]}'))
    fx = Decimal(os.getenv('AI_USD_JPY', '200'))  # conservative configurable estimate, not live FX
    stop = Decimal(os.getenv('AI_STOP_JPY', '2400'))
    warning = Decimal(os.getenv('AI_WARNING_JPY', '1800'))
    budget = Decimal(os.getenv('AI_BUDGET_JPY', '3000'))
    if not 0 < warning < stop <= budget or not fx.is_finite() or fx <= 0:
        raise ValueError('Invalid AI budget settings')
    normalized = {}
    for model, pair in prices.items():
        rates = tuple(Decimal(str(rate)) for rate in pair)
        if len(rates) != 2 or any(not rate.is_finite() or rate <= 0 for rate in rates):
            raise ValueError('Invalid model pricing')
        normalized[model] = rates
    return dict(prices=normalized, fx=fx, stop=stop, warning=warning, budget=budget)


def cost(input_tokens, output_tokens, input_rate, output_rate, fx):
    return ((Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate)
            * fx / Decimal(1000000)).quantize(Decimal('0.000001'), rounding='ROUND_CEILING')
