# FIFO Cost Basis

Dependency-free Decimal FIFO lot matching with realized PnL events, open lots, partial-coverage reporting, and explicit quality flags.

## Contract

Operations use a **positive amount convention**:

- buy: `gross_amount` is acquisition cost before fee;
- sell: `gross_amount` is proceeds before fee;
- `fee` is a separate non-negative amount;
- `quantity` is positive for buy/sell;
- `(occurred_at, sequence, operation_id)` defines deterministic order;
- operation IDs and sequence values are globally unique within one computation.

Buy fee is included in lot cost. Sell fee is allocated per unit and only the matched share contributes to the realized event. Unmatched sells never invent cost or PnL: they produce `partial` coverage and a critical quality flag.

## Installation

```bash
pip install https://github.com/FrankFMY/fifo-cost-basis/releases/download/v0.1.0/fifo_cost_basis-0.1.0-py3-none-any.whl
```

## Example

```python
from datetime import datetime, timezone
from decimal import Decimal as D
from fifo_cost_basis import Operation, compute_fifo

result = compute_fifo(
    [
        Operation(
            "buy-1", "ASSET", "buy", D("10"), D("1000"), D("10"), "USD", datetime(2026, 1, 1, tzinfo=timezone.utc), 1
        ),
        Operation(
            "sell-1", "ASSET", "sell", D("4"), D("500"), D("5"), "USD", datetime(2026, 2, 1, tzinfo=timezone.utc), 2
        ),
    ]
)

print(result.realized_pnl)
print(result.open_quantity)
```

## Quality and currency boundaries

- Top-level totals include only `base_currency`.
- Multi-currency input requires an explicit base currency; no FX conversion is performed.
- Corporate actions, adjustments, tax rows, and standalone fee rows are reported as quality flags but not applied to lots.
- Transfers, short positions, splits, mergers, corrections, wash-sale rules, tax jurisdictions, and broker-specific tax-lot elections are outside v0.1.
- Inputs are bounded to finite Decimal values with at most 100 significant digits and exponent magnitude 100.

This package is analytical infrastructure. It is **not tax advice**, an official tax-lot calculator, broker reconciliation, or a substitute for jurisdiction-specific accounting review.

## Development

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --editable . ruff build twine
python -m ruff format --check .
python -m ruff check .
python -m unittest discover -s tests -v
python -m build
python -m twine check dist/*
```

## Author and contact

**Artem Prianishnikov** · [FrankFMY](https://github.com/FrankFMY)

[Website](https://frankfmy.com) · [Telegram](https://t.me/FrankFMY) · [LinkedIn](https://www.linkedin.com/in/frankfmy/) · [X](https://x.com/FrankFMY) · [npm profile](https://www.npmjs.com/~frankfmy)

Email: [frankfmy@proton.me](mailto:frankfmy@proton.me) · [Pryanishnikovartem@gmail.com](mailto:Pryanishnikovartem@gmail.com)

## License

MIT © 2026 Artem Prianishnikov. See [LICENSE](LICENSE).
