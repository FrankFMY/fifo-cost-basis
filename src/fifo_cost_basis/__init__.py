from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal, Inexact, Rounded, localcontext
from typing import Literal

OperationKind = Literal["buy", "sell", "corporate_action", "adjustment", "tax", "fee"]
Severity = Literal["info", "warning", "critical"]
_ZERO = Decimal("0")
_MAX_ABS = Decimal("1e50")
_MAX_DIGITS = 100
_MAX_EXPONENT = 100
_PRECISION = 256
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")
_ALLOWED_KINDS = {"buy", "sell", "corporate_action", "adjustment", "tax", "fee"}


@dataclass(frozen=True, slots=True)
class Operation:
    operation_id: str
    instrument: str
    kind: str
    quantity: Decimal
    gross_amount: Decimal
    fee: Decimal
    currency: str
    occurred_at: datetime
    sequence: int


@dataclass(frozen=True, slots=True)
class QualityFlag:
    code: str
    severity: Severity
    count: int
    message: str


@dataclass(frozen=True, slots=True)
class RealizedEvent:
    operation_id: str
    instrument: str
    currency: str
    occurred_at: datetime
    sold_quantity: Decimal
    matched_quantity: Decimal
    unmatched_quantity: Decimal
    proceeds: Decimal
    allocated_cost: Decimal
    commission_allocated: Decimal
    realized_pnl: Decimal


@dataclass(frozen=True, slots=True)
class OpenLot:
    quantity: Decimal
    unit_cost: Decimal
    opened_at: datetime
    source_operation_id: str


@dataclass(frozen=True, slots=True)
class InstrumentCostBasis:
    instrument: str
    currency: str
    buy_quantity: Decimal
    sell_quantity: Decimal
    matched_sell_quantity: Decimal
    unmatched_sell_quantity: Decimal
    realized_proceeds: Decimal
    allocated_cost: Decimal
    realized_pnl: Decimal
    open_quantity: Decimal
    open_cost: Decimal
    average_open_cost: Decimal | None
    lots: tuple[OpenLot, ...]


@dataclass(frozen=True, slots=True)
class FifoResult:
    method: str
    base_currency: str
    coverage: Literal["no_sales", "partial", "full"]
    operations_count: int
    realized_proceeds: Decimal
    allocated_cost: Decimal
    commission_allocated: Decimal
    realized_pnl: Decimal
    matched_sell_quantity: Decimal
    unmatched_sell_quantity: Decimal
    open_quantity: Decimal
    open_cost: Decimal
    instruments: tuple[InstrumentCostBasis, ...]
    events: tuple[RealizedEvent, ...]
    quality_flags: tuple[QualityFlag, ...]
    limitations: tuple[str, ...]


@dataclass(slots=True)
class _Lot:
    quantity: Decimal
    unit_cost: Decimal
    opened_at: datetime
    source_operation_id: str


@dataclass(slots=True)
class _Accumulator:
    instrument: str
    currency: str
    buy_quantity: Decimal = _ZERO
    sell_quantity: Decimal = _ZERO
    matched_sell_quantity: Decimal = _ZERO
    unmatched_sell_quantity: Decimal = _ZERO
    realized_proceeds: Decimal = _ZERO
    allocated_cost: Decimal = _ZERO
    realized_pnl: Decimal = _ZERO
    lots: deque[_Lot] | None = None

    def __post_init__(self) -> None:
        if self.lots is None:
            self.lots = deque()


def _decimal(value: object, name: str, *, positive: bool = False) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if value.copy_abs() > _MAX_ABS:
        raise ValueError(f"{name} exceeds the supported magnitude")
    parts = value.as_tuple()
    if not isinstance(parts.exponent, int):
        raise ValueError(f"{name} has an unsupported exponent")
    if len(parts.digits) > _MAX_DIGITS or abs(parts.exponent) > _MAX_EXPONENT:
        raise ValueError(f"{name} exceeds the supported precision")
    if positive and value <= 0:
        raise ValueError(f"{name} must be positive")
    if not positive and value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _validate_operations(operations: tuple[Operation, ...]) -> tuple[Operation, ...]:
    ids: set[str] = set()
    sequences: set[int] = set()
    validated: list[Operation] = []
    for operation in operations:
        if not isinstance(operation, Operation):
            raise ValueError("operations must use the declared Operation contract")
        if not isinstance(operation.operation_id, str) or _IDENTIFIER.fullmatch(operation.operation_id) is None:
            raise ValueError("operation_id must be a bounded portable identifier")
        if operation.operation_id in ids:
            raise ValueError("operation_id values must be unique")
        ids.add(operation.operation_id)
        if type(operation.sequence) is not int or operation.sequence < 0 or operation.sequence in sequences:
            raise ValueError("sequence values must be unique non-negative integers")
        sequences.add(operation.sequence)
        if not isinstance(operation.instrument, str) or _IDENTIFIER.fullmatch(operation.instrument) is None:
            raise ValueError("instrument must be a bounded portable identifier")
        if not isinstance(operation.currency, str) or _IDENTIFIER.fullmatch(operation.currency) is None:
            raise ValueError("currency must be a bounded portable identifier")
        if operation.kind not in _ALLOWED_KINDS:
            raise ValueError("unsupported operation kind")
        if not isinstance(operation.occurred_at, datetime) or operation.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        if operation.kind in {"buy", "sell"}:
            _decimal(operation.quantity, "quantity", positive=True)
        else:
            _decimal(operation.quantity, "quantity")
        _decimal(operation.gross_amount, "gross_amount")
        _decimal(operation.fee, "fee")
        validated.append(operation)
    return tuple(
        sorted(
            validated,
            key=lambda item: (item.occurred_at.astimezone(timezone.utc), item.sequence, item.operation_id),
        )
    )


def _flag(code: str, severity: Severity, count: int, message: str) -> QualityFlag:
    return QualityFlag(code=code, severity=severity, count=count, message=message)


def compute_fifo(
    operations: list[Operation] | tuple[Operation, ...], *, base_currency: str | None = None
) -> FifoResult:
    with localcontext() as context:
        context.prec = _PRECISION
        context.rounding = ROUND_HALF_EVEN
        context.traps[Inexact] = False
        context.traps[Rounded] = False
        return _compute_fifo(operations, base_currency=base_currency)


def _compute_fifo(operations: list[Operation] | tuple[Operation, ...], *, base_currency: str | None) -> FifoResult:
    if not isinstance(operations, (list, tuple)):
        raise ValueError("operations must be a list or tuple")
    ordered = _validate_operations(tuple(operations))
    currencies = sorted({operation.currency.upper() for operation in ordered})
    if base_currency is None:
        if len(currencies) > 1:
            raise ValueError("base_currency is required when operations contain multiple currencies")
        resolved_base = currencies[0] if currencies else "XXX"
    else:
        if not isinstance(base_currency, str) or _IDENTIFIER.fullmatch(base_currency.strip().upper()) is None:
            raise ValueError("base_currency must be a bounded portable identifier")
        resolved_base = base_currency.strip().upper()

    accumulators: dict[tuple[str, str], _Accumulator] = {}
    events: list[RealizedEvent] = []
    kind_counts: dict[str, int] = defaultdict(int)
    with localcontext() as context:
        context.prec = _PRECISION
        for operation in ordered:
            kind_counts[operation.kind] += 1
            if operation.kind not in {"buy", "sell"}:
                continue
            key = (operation.instrument, operation.currency.upper())
            accumulator = accumulators.setdefault(key, _Accumulator(*key))
            assert accumulator.lots is not None
            if operation.kind == "buy":
                unit_cost = (operation.gross_amount + operation.fee) / operation.quantity
                accumulator.buy_quantity += operation.quantity
                accumulator.lots.append(
                    _Lot(operation.quantity, unit_cost, operation.occurred_at, operation.operation_id)
                )
                continue

            remaining = operation.quantity
            matched = _ZERO
            allocated_cost = _ZERO
            proceeds = _ZERO
            commission_allocated = _ZERO
            net_proceeds = operation.gross_amount - operation.fee
            proceeds_per_unit = net_proceeds / operation.quantity
            fee_per_unit = operation.fee / operation.quantity
            while remaining > 0 and accumulator.lots:
                lot = accumulator.lots[0]
                taken = min(remaining, lot.quantity)
                matched += taken
                allocated_cost += taken * lot.unit_cost
                proceeds += taken * proceeds_per_unit
                commission_allocated += taken * fee_per_unit
                lot.quantity -= taken
                remaining -= taken
                if lot.quantity == 0:
                    accumulator.lots.popleft()
            pnl = proceeds - allocated_cost
            accumulator.sell_quantity += operation.quantity
            accumulator.matched_sell_quantity += matched
            accumulator.unmatched_sell_quantity += remaining
            accumulator.realized_proceeds += proceeds
            accumulator.allocated_cost += allocated_cost
            accumulator.realized_pnl += pnl
            events.append(
                RealizedEvent(
                    operation.operation_id,
                    operation.instrument,
                    operation.currency.upper(),
                    operation.occurred_at,
                    operation.quantity,
                    matched,
                    remaining,
                    proceeds,
                    allocated_cost,
                    commission_allocated,
                    pnl,
                )
            )

    instruments: list[InstrumentCostBasis] = []
    for key in sorted(accumulators):
        accumulator = accumulators[key]
        assert accumulator.lots is not None
        open_quantity = sum((lot.quantity for lot in accumulator.lots), _ZERO)
        open_cost = sum((lot.quantity * lot.unit_cost for lot in accumulator.lots), _ZERO)
        instruments.append(
            InstrumentCostBasis(
                accumulator.instrument,
                accumulator.currency,
                accumulator.buy_quantity,
                accumulator.sell_quantity,
                accumulator.matched_sell_quantity,
                accumulator.unmatched_sell_quantity,
                accumulator.realized_proceeds,
                accumulator.allocated_cost,
                accumulator.realized_pnl,
                open_quantity,
                open_cost,
                open_cost / open_quantity if open_quantity else None,
                tuple(
                    OpenLot(lot.quantity, lot.unit_cost, lot.opened_at, lot.source_operation_id)
                    for lot in accumulator.lots
                ),
            )
        )

    base_instruments = [item for item in instruments if item.currency == resolved_base]
    base_events = [event for event in events if event.currency == resolved_base]
    all_unmatched = sum((event.unmatched_quantity for event in events), _ZERO)
    base_unmatched = sum((event.unmatched_quantity for event in base_events), _ZERO)
    flags: list[QualityFlag] = []
    if all_unmatched > 0:
        flags.append(
            _flag(
                "unmatched_sell_lots",
                "critical",
                sum(event.unmatched_quantity > 0 for event in events),
                "Sell quantity could not be matched to imported buy lots.",
            )
        )
    if kind_counts["corporate_action"]:
        flags.append(
            _flag(
                "corporate_actions_present",
                "warning",
                kind_counts["corporate_action"],
                "Corporate actions are not applied to FIFO lots.",
            )
        )
    if kind_counts["adjustment"]:
        flags.append(
            _flag(
                "adjustments_present",
                "warning",
                kind_counts["adjustment"],
                "Broker adjustments are not applied to FIFO lots.",
            )
        )
    non_base_count = sum(operation.currency.upper() != resolved_base for operation in ordered)
    if non_base_count:
        flags.append(
            _flag(
                "non_base_currency_operations_present",
                "warning",
                non_base_count,
                "Top-level totals exclude currencies other than base_currency; no FX conversion is performed.",
            )
        )
    if kind_counts["tax"]:
        flags.append(
            _flag(
                "tax_operations_present",
                "info",
                kind_counts["tax"],
                "Tax operations are reported but not applied to realized PnL.",
            )
        )
    if kind_counts["fee"]:
        flags.append(
            _flag(
                "standalone_fee_operations_present",
                "info",
                kind_counts["fee"],
                "Standalone fee operations are reported but not allocated to lots.",
            )
        )

    coverage: Literal["no_sales", "partial", "full"] = (
        "no_sales" if not events else "partial" if all_unmatched else "full"
    )
    limitations = (
        "FIFO uses caller-supplied buy/sell operations and a positive gross/fee convention.",
        "Corporate actions, transfers, short positions, corrections, taxes, standalone fees, "
        "and FX conversion are not applied.",
        "The output is analytical infrastructure, not tax advice or a jurisdiction-specific tax-lot calculation.",
    )
    return FifoResult(
        method="fifo-positive-amounts-v1",
        base_currency=resolved_base,
        coverage=coverage,
        operations_count=len(ordered),
        realized_proceeds=sum((item.realized_proceeds for item in base_instruments), _ZERO),
        allocated_cost=sum((item.allocated_cost for item in base_instruments), _ZERO),
        commission_allocated=sum((event.commission_allocated for event in base_events), _ZERO),
        realized_pnl=sum((item.realized_pnl for item in base_instruments), _ZERO),
        matched_sell_quantity=sum((item.matched_sell_quantity for item in base_instruments), _ZERO),
        unmatched_sell_quantity=base_unmatched,
        open_quantity=sum((item.open_quantity for item in base_instruments), _ZERO),
        open_cost=sum((item.open_cost for item in base_instruments), _ZERO),
        instruments=tuple(instruments),
        events=tuple(events),
        quality_flags=tuple(flags),
        limitations=limitations,
    )


__all__ = [
    "FifoResult",
    "InstrumentCostBasis",
    "OpenLot",
    "Operation",
    "QualityFlag",
    "RealizedEvent",
    "compute_fifo",
]
