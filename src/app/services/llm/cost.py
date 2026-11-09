"""Token usage cost estimation based on official model pricing tables."""

from decimal import ROUND_HALF_UP, Decimal

# Documented pricing per 1,000,000 tokens (USD)
# Source: Google Cloud & Gemini Developer API standard tier pricing
GEMINI_PRICING_PER_MILLION: dict[str, dict[str, float]] = {
    # Gemini 2.5 Series
    "gemini-2.5-flash": {
        "input": 0.075,
        "output": 0.30,
    },
    "gemini-2.5-pro": {
        "input": 1.25,
        "output": 5.00,
    },
    # Gemini 1.5 Series
    "gemini-1.5-flash": {
        "input": 0.075,
        "output": 0.30,
    },
    "gemini-1.5-pro": {
        "input": 1.25,
        "output": 5.00,
    },
    "gemini-1.5-flash-8b": {
        "input": 0.0375,
        "output": 0.15,
    },
}


def calculate_token_cost(
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> Decimal | None:
    """Calculate estimated cost in USD for token consumption.

    Args:
        model_name: Gemini model identifier (e.g. 'gemini-2.5-flash').
        prompt_tokens: Input tokens consumed.
        completion_tokens: Generated output tokens.

    Returns:
        Decimal cost rounded to 6 decimal places, or None if model is unpriced.
        Never invents or fabricates pricing for unrecognized models.
    """
    # Normalize model string (strip 'models/' prefix if present)
    normalized_model = model_name.removeprefix("models/").lower()

    # Find matching pricing configuration (prefix match handles versions like -001, -002, -latest)
    pricing = None
    for key, rates in GEMINI_PRICING_PER_MILLION.items():
        if normalized_model.startswith(key):
            pricing = rates
            break

    if not pricing:
        return None

    # Calculate input and output costs
    input_cost = (Decimal(prompt_tokens) / Decimal(1_000_000)) * Decimal(str(pricing["input"]))
    output_cost = (Decimal(completion_tokens) / Decimal(1_000_000)) * Decimal(
        str(pricing["output"])
    )
    total_cost = input_cost + output_cost

    return total_cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
