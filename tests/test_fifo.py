from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext
from zoneinfo import ZoneInfo

from fifo_cost_basis import Operation, compute_fifo

D = Decimal


def operation(
    operation_id: str,
    kind: str,
    quantity: str,
    gross: str,
    fee: str,
    sequence: int,
    *,
    instrument: str = "SBER",
    currency: str = "RUB",
) -> Operation:
    return Operation(
        operation_id=operation_id,
        instrument=instrument,
        kind=kind,
        quantity=D(quantity),
        gross_amount=D(gross),
        fee=D(fee),
        currency=currency,
        occurred_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        sequence=sequence,
    )


class FifoCostBasisTest(unittest.TestCase):
    def test_matches_fifo_lots_and_allocates_sell_fee_proportionally(self):
        result = compute_fifo(
            [
                operation("buy-1", "buy", "10", "1000", "10", 1),
                operation("buy-2", "buy", "5", "600", "6", 2),
                operation("sell-1", "sell", "12", "1500", "15", 3),
            ]
        )
        self.assertEqual(result.coverage, "full")
        self.assertEqual(result.realized_proceeds, D("1485"))
        self.assertEqual(result.allocated_cost, D("1252.4"))
        self.assertEqual(result.commission_allocated, D("15"))
        self.assertEqual(result.realized_pnl, D("232.6"))
        self.assertEqual(result.matched_sell_quantity, D("12"))
        self.assertEqual(result.unmatched_sell_quantity, D("0"))
        self.assertEqual(result.open_quantity, D("3"))
        self.assertEqual(result.open_cost, D("363.6"))
        self.assertEqual(result.instruments[0].average_open_cost, D("121.2"))
        self.assertEqual(result.quality_flags, ())

    def test_reports_partial_coverage_without_inventing_cost_for_unmatched_sell(self):
        result = compute_fifo([operation("sell-1", "sell", "2", "500", "5", 1)])
        self.assertEqual(result.coverage, "partial")
        self.assertEqual(result.realized_pnl, D("0"))
        self.assertEqual(result.unmatched_sell_quantity, D("2"))
        self.assertEqual(result.events[0].matched_quantity, D("0"))
        self.assertEqual(result.events[0].unmatched_quantity, D("2"))
        self.assertIn("unmatched_sell_lots", {flag.code for flag in result.quality_flags})

    def test_same_timestamp_sequence_is_authoritative_and_duplicate_identity_fails(self):
        full = compute_fifo(
            [
                operation("sell", "sell", "1", "120", "0", 2),
                operation("buy", "buy", "1", "100", "0", 1),
            ]
        )
        self.assertEqual(full.coverage, "full")
        self.assertEqual(full.realized_pnl, D("20"))
        with self.assertRaises(ValueError):
            compute_fifo(
                [
                    operation("a", "buy", "1", "100", "0", 1),
                    operation("b", "sell", "1", "120", "0", 1),
                ]
            )
        with self.assertRaises(ValueError):
            compute_fifo([operation("same", "buy", "1", "100", "0", 1), operation("same", "buy", "1", "100", "0", 2)])

    def test_reports_unsupported_operation_and_currency_quality_flags(self):
        result = compute_fifo(
            [
                operation("buy", "buy", "1", "100", "0", 1),
                operation("split", "corporate_action", "0", "0", "0", 2),
                operation("adjust", "adjustment", "0", "0", "0", 3),
                operation("usd", "buy", "1", "10", "0", 4, instrument="AAPL", currency="USD"),
            ],
            base_currency="RUB",
        )
        flags = {flag.code: flag.count for flag in result.quality_flags}
        self.assertEqual(flags["corporate_actions_present"], 1)
        self.assertEqual(flags["adjustments_present"], 1)
        self.assertEqual(flags["non_base_currency_operations_present"], 1)

    def test_rejects_non_finite_negative_or_ambiguous_inputs(self):
        invalid = [
            operation("nan", "buy", "NaN", "10", "0", 1),
            operation("negative", "buy", "-1", "10", "0", 1),
            operation("fee", "buy", "1", "10", "-1", 1),
            operation("kind", "unknown", "1", "10", "0", 1),
        ]
        for item in invalid:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    compute_fifo([item])

    def test_results_do_not_depend_on_caller_decimal_context_or_traps(self):
        operations = [
            operation("buy", "buy", "7", "1", "0", 1),
            operation("sell", "sell", "1", "1", "0", 2),
        ]
        with localcontext() as low:
            low.prec = 6
            low.rounding = ROUND_DOWN
            low.traps[Inexact] = True
            low_result = compute_fifo(operations)
        with localcontext() as high:
            high.prec = 80
            high_result = compute_fifo(operations)
        self.assertEqual(low_result, high_result)

    def test_dst_fold_order_uses_absolute_instant_before_sequence(self):
        zone = ZoneInfo("America/New_York")
        buy = Operation(
            "buy", "ASSET", "buy", D("1"), D("100"), D("0"), "USD", datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0), 2
        )
        sell = Operation(
            "sell",
            "ASSET",
            "sell",
            D("1"),
            D("120"),
            D("0"),
            "USD",
            datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1),
            1,
        )
        result = compute_fifo([sell, buy])
        self.assertEqual(result.coverage, "full")
        self.assertEqual(result.matched_sell_quantity, D("1"))
        self.assertEqual(result.realized_pnl, D("20"))

    def test_top_level_unmatched_quantity_is_base_currency_only(self):
        result = compute_fifo(
            [
                operation("usd-buy", "buy", "1", "100", "0", 1, instrument="USD-ASSET", currency="USD"),
                operation("usd-sell", "sell", "1", "120", "0", 2, instrument="USD-ASSET", currency="USD"),
                operation("eur-sell", "sell", "7", "70", "0", 3, instrument="EUR-ASSET", currency="EUR"),
            ],
            base_currency="USD",
        )
        self.assertEqual(result.coverage, "partial")
        self.assertEqual(result.matched_sell_quantity, D("1"))
        self.assertEqual(result.unmatched_sell_quantity, D("0"))
        self.assertEqual(
            next(item for item in result.instruments if item.currency == "EUR").unmatched_sell_quantity, D("7")
        )


if __name__ == "__main__":
    unittest.main()
