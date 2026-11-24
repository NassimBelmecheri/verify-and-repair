from __future__ import annotations

import math
from decimal import Decimal

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)
PHASES = {"generation", "repair", "review", "other"}


def validate_receipt(raw: dict) -> dict:
    required = {"request_id", "model", "phase", *TOKEN_FIELDS}
    if (
        not isinstance(raw, dict)
        or not required <= raw.keys()
        or set(raw) - (required | {"cost", "currency"})
    ):
        raise ValueError(
            "Usage receipts require request_id, model, phase and all token fields"
        )
    for name in ("request_id", "model"):
        if not isinstance(raw[name], str) or not raw[name].strip():
            raise ValueError(f"{name} must be a nonempty string")
    if raw["phase"] not in PHASES:
        raise ValueError("Usage phase must be generation, repair, review or other")
    for name in TOKEN_FIELDS:
        if type(raw[name]) is not int or raw[name] < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    if raw["cache_read_tokens"] + raw["cache_write_tokens"] > raw["input_tokens"]:
        raise ValueError(
            "Input tokens must include both cache-read and cache-write tokens"
        )
    if raw["reasoning_tokens"] > raw["output_tokens"]:
        raise ValueError("Output tokens must include reasoning tokens")
    receipt = dict(raw)
    if "cost" in raw:
        if (
            type(raw["cost"]) not in {int, float}
            or not math.isfinite(raw["cost"])
            or raw["cost"] < 0
            or not isinstance(raw.get("currency"), str)
            or not raw["currency"].strip()
        ):
            raise ValueError(
                "Cost requires a finite nonnegative amount and explicit currency or billing unit"
            )
    elif "currency" in raw:
        raise ValueError("Currency requires cost")
    return receipt


def merge_receipts(existing: list[dict], incoming: list[dict]) -> list[dict]:
    if not isinstance(incoming, list):
        raise TypeError("Usage export must be a JSON array")
    merged = {item["request_id"]: item for item in existing}
    for item in incoming:
        item = validate_receipt(item)
        request_id = item["request_id"]
        if request_id in merged and merged[request_id] != item:
            raise ValueError(f"Conflicting usage for request ID: {request_id}")
        merged[request_id] = item
    return list(merged.values())


def usage_summary(ledger: dict) -> dict:
    receipts = ledger.get("receipts", [])
    totals = {name: sum(item[name] for item in receipts) for name in TOKEN_FIELDS}
    costs: dict[str, Decimal] = {}
    for item in receipts:
        if "cost" in item:
            unit = item["currency"]
            costs[unit] = costs.get(unit, Decimal(0)) + Decimal(str(item["cost"]))
    complete = bool(ledger.get("task_complete_attested"))
    accepted = ledger.get("accepted")
    costs_complete = (
        complete and bool(receipts) and all("cost" in item for item in receipts)
    )
    return {
        "coverage": "task_complete_attested" if complete else "partial_or_missing",
        "provenance": "Imported host receipts; totals and task completeness are not independently verified by the server.",
        "requests": len(receipts) if receipts else None,
        "tokens": totals if receipts else None,
        "phases": {
            phase: {
                "requests": sum(item["phase"] == phase for item in receipts),
                **{
                    name: sum(item[name] for item in receipts if item["phase"] == phase)
                    for name in TOKEN_FIELDS
                },
            }
            for phase in sorted({item["phase"] for item in receipts})
        },
        "reported_cost_by_unit": {unit: float(value) for unit, value in costs.items()},
        "cost_coverage_complete": costs_complete,
        "accepted": accepted,
        "cost_per_accepted_task_by_unit": (
            {unit: float(value) for unit, value in costs.items()}
            if accepted is True and costs_complete
            else None
        ),
        "token_semantics": "Input includes cache-read/write; output includes reasoning. Subcounts must not be added again.",
        "imports": ledger.get("imports", []),
    }


def task_report(states: list[dict]) -> dict:
    ledgers = [state.get("usage", {}) for state in states]
    all_receipts = [item for ledger in ledgers for item in ledger.get("receipts", [])]
    combined = merge_receipts([], all_receipts)
    overlapping = len(combined) != len(all_receipts)
    complete = (
        bool(states)
        and not overlapping
        and all(
            ledger.get("task_complete_attested") and ledger.get("receipts")
            for ledger in ledgers
        )
    )
    accepted = sum(ledger.get("accepted") is True for ledger in ledgers)
    summary = usage_summary({"receipts": combined, "task_complete_attested": complete})
    summary.pop("accepted")
    summary["tasks"] = len(states)
    summary["accepted_tasks"] = accepted
    summary["acceptance_unknown_tasks"] = sum(
        ledger.get("accepted") is None for ledger in ledgers
    )
    summary["overlapping_request_ids"] = overlapping
    summary["cost_per_accepted_task_by_unit"] = (
        {
            unit: float(Decimal(str(cost)) / accepted)
            for unit, cost in summary["reported_cost_by_unit"].items()
        }
        if accepted
        and summary["cost_coverage_complete"]
        and not summary["acceptance_unknown_tasks"]
        else None
    )
    summary["denominator_note"] = (
        "All imported costs, including rejected tasks, divided by explicitly accepted tasks. "
        "Incomplete task/cost coverage, unknown acceptance or overlapping request IDs suppresses the metric."
    )
    return summary
