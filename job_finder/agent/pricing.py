"""Euro prices per million tokens, so every model call can be booked exactly."""

from dataclasses import dataclass
from decimal import Decimal

MILLION = Decimal(1_000_000)


@dataclass(frozen=True)
class Usage:
    """Token counts the API reports for one call.

    input_tokens includes cached_input_tokens, and output_tokens includes
    reasoning_tokens: the model's hidden thinking is billed like visible output.
    """

    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int = 0


@dataclass(frozen=True)
class Price:
    input_eur: Decimal
    cached_input_eur: Decimal
    output_eur: Decimal


# Azure list prices for Global Standard deployments (Retail Prices API,
# France Central, 25.09.2026). Data Zone EU costs about 10 % more.
PRICES = {"gpt-5-mini": Price(Decimal("0.2147"), Decimal("0.0215"), Decimal("1.7173"))}


def call_cost(model, usage):
    """Return the euro cost of one call; unknown models and odd counts raise ValueError."""
    price = PRICES.get(model)
    if price is None:
        raise ValueError(f"Kein Preis für Modell {model} hinterlegt")
    counts = (
        usage.input_tokens,
        usage.cached_input_tokens,
        usage.output_tokens,
        usage.reasoning_tokens,
    )
    if (
        any(isinstance(count, bool) or not isinstance(count, int) for count in counts)
        or not 0 <= usage.cached_input_tokens <= usage.input_tokens
        or not 0 <= usage.reasoning_tokens <= usage.output_tokens
    ):
        raise ValueError(f"Ungültige Token-Angaben: {usage}")
    uncached = usage.input_tokens - usage.cached_input_tokens
    return (
        uncached * price.input_eur
        + usage.cached_input_tokens * price.cached_input_eur
        + usage.output_tokens * price.output_eur
    ) / MILLION
