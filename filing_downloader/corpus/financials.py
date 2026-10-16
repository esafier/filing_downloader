"""
Derived financials from SEC XBRL Company Facts.

One Markdown file with income statement, balance sheet and cash flow as
clean tables across every available period. Already structured at source:
no HTML parsing, no mangled tables, no guessing. This is what stops the
model reconstructing financials out of prose.
"""

from datetime import date

from ..edgar import fetch_company_facts

# Each line item lists its XBRL tags in priority order. Companies switch
# tags between years (revenue alone has four common spellings), so a single
# tag silently produces gaps in the series.
INCOME_STATEMENT_CONCEPTS = [
    ("Revenue", ["RevenueFromContractWithCustomerExcludingAssessedTax",
                 "RevenueFromContractWithCustomerIncludingAssessedTax",
                 "Revenues", "SalesRevenueNet", "SalesRevenueGoodsNet"]),
    ("Cost of revenue", ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"]),
    ("Gross profit", ["GrossProfit"]),
    ("R&D expense", ["ResearchAndDevelopmentExpense"]),
    ("SG&A expense", ["SellingGeneralAndAdministrativeExpense",
                      "GeneralAndAdministrativeExpense"]),
    ("Operating income", ["OperatingIncomeLoss"]),
    ("Pre-tax income", ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]),
    ("Income tax expense", ["IncomeTaxExpenseBenefit"]),
    ("Net income", ["NetIncomeLoss", "ProfitLoss"]),
    ("Diluted EPS", ["EarningsPerShareDiluted"]),
    ("Diluted shares", ["WeightedAverageNumberOfDilutedSharesOutstanding"]),
]

BALANCE_SHEET_CONCEPTS = [
    ("Cash & equivalents", ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsAndShortTermInvestments"]),
    ("Total current assets", ["AssetsCurrent"]),
    ("Total assets", ["Assets"]),
    ("Total current liabilities", ["LiabilitiesCurrent"]),
    ("Long-term debt", ["LongTermDebtNoncurrent", "LongTermDebt"]),
    ("Total liabilities", ["Liabilities"]),
    ("Stockholders equity", ["StockholdersEquity",
                             "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
]

CASH_FLOW_CONCEPTS = [
    ("Operating cash flow", ["NetCashProvidedByUsedInOperatingActivities",
                             "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    ("Capital expenditure", ["PaymentsToAcquirePropertyPlantAndEquipment",
                             "PaymentsToAcquireProductiveAssets"]),
    ("Share repurchases", ["PaymentsForRepurchaseOfCommonStock"]),
    ("Dividends paid", ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"]),
]

# Forms whose facts count for each period type. 20-F/40-F join in Phase 3.
ANNUAL_FORMS = ("10-K", "20-F")
QUARTERLY_FORMS = ("10-Q",)
INSTANT_FORMS = ("10-K", "10-Q", "20-F")


def days_between(start, end):
    """Days between two YYYY-MM-DD strings, or -1 if either is malformed."""
    try:
        y1, m1, d1 = (int(x) for x in start.split("-"))
        y2, m2, d2 = (int(x) for x in end.split("-"))
        return (date(y2, m2, d2) - date(y1, m1, d1)).days
    except (ValueError, TypeError, AttributeError):
        return -1


def pick_observations(facts, tags, period_type):
    """
    Pulls one line item's time series out of Company Facts.

    Three things have to be handled or the numbers come out wrong:
      - The same period appears many times (original + every restatement);
        the LATEST FILED value wins.
      - Annual and quarterly figures are mixed; they are told apart by how
        long the period is.
      - Units vary; USD (or USD/shares, shares) is kept.

    Merges ACROSS tags: higher-priority tags win where both have a value,
    lower-priority tags fill the gaps. Returns ({period_end: value},
    "tagA / tagB", unit).
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    merged, tags_hit, unit_hit = {}, [], None

    for tag in tags:
        if tag not in us_gaap:
            continue
        units = us_gaap[tag].get("units", {})
        unit_key = next((u for u in ("USD", "USD/shares", "shares") if u in units), None)
        if unit_key is None:
            continue

        best = {}
        for entry in units[unit_key]:
            end, start = entry.get("end"), entry.get("start")
            filed, form, value = entry.get("filed", ""), entry.get("form", ""), entry.get("val")
            if end is None or value is None:
                continue
            if period_type == "instant":
                if start is not None or form not in INSTANT_FORMS:
                    continue
            else:
                if start is None:
                    continue
                days = days_between(start, end)
                if period_type == "annual":
                    if not (330 <= days <= 400) or form not in ANNUAL_FORMS:
                        continue
                elif not (80 <= days <= 100) or form not in QUARTERLY_FORMS:
                    continue
            previous = best.get(end)
            if previous is None or filed > previous["filed"]:
                best[end] = {"val": value, "filed": filed}

        if best:
            tags_hit.append(tag)
            unit_hit = unit_hit or unit_key
            for period, entry in best.items():
                merged.setdefault(period, entry["val"])

    if merged:
        return merged, " / ".join(tags_hit), unit_hit
    return {}, None, None


def format_value(value, unit):
    """Formats a raw XBRL number for display (USD in millions)."""
    if value is None:
        return ""
    if unit == "USD/shares":
        return f"{value:,.2f}"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:,.1f}"
    return f"{value:,.0f}"


def build_table(facts, concepts, period_type, max_periods):
    """Builds one Markdown table (income statement / balance sheet / cash flow)."""
    series, tags_used, units_used, all_periods = {}, {}, {}, set()
    for label, tags in concepts:
        values, tag, unit = pick_observations(facts, tags, period_type)
        if not values:
            continue
        series[label], tags_used[label], units_used[label] = values, tag, unit
        all_periods.update(values.keys())
    if not series:
        return None, {}

    periods = sorted(sorted(all_periods, reverse=True)[:max_periods])  # oldest -> newest
    lines = ["| Line item (USD millions unless noted) | " + " | ".join(periods) + " |",
             "| --- | " + " | ".join(["---"] * len(periods)) + " |"]
    for label, _ in concepts:
        if label not in series:
            continue
        unit = units_used[label]
        cells = [format_value(series[label].get(p), unit) for p in periods]
        display = label
        if unit == "USD/shares":
            display = f"{label} (USD)"
        elif unit == "shares":
            display = f"{label} (millions)"
        lines.append(f"| {display} | " + " | ".join(cells) + " |")
    return "\n".join(lines), tags_used


def build_financials_timeseries(cik, ticker, company_name):
    """Returns the Markdown for the time-series file, or None without XBRL data."""
    facts = fetch_company_facts(cik)
    if facts is None:
        return None

    parts = [
        f"# {ticker} - Financial statements time series",
        "",
        f"Company: {company_name}  ",
        f"Source: SEC XBRL Company Facts (CIK {cik:010d})  ",
        "Values are as most recently restated by the company.",
        "",
        "> Segment revenue is deliberately NOT in this file. Segment data is",
        "> dimensional and Company Facts drops the dimensions, so there is no",
        "> reliable series to pull. See the 10-K notes file for segments.",
        "",
        "> The quarterly table has no Q4 column. That is correct, not a gap:",
        "> companies file a 10-K instead of a Q4 10-Q, so Q4 is only ever",
        "> reported as the annual figure less the first three quarters.",
        "",
    ]
    all_tags = {}
    for heading, concepts, period_type, limit in [
        ("Income statement - annual", INCOME_STATEMENT_CONCEPTS, "annual", 6),
        ("Income statement - quarterly", INCOME_STATEMENT_CONCEPTS, "quarterly", 12),
        ("Balance sheet", BALANCE_SHEET_CONCEPTS, "instant", 8),
        ("Cash flow - annual", CASH_FLOW_CONCEPTS, "annual", 6),
    ]:
        table, tags = build_table(facts, concepts, period_type, limit)
        if table is None:
            continue
        parts += [f"## {heading}", "", table, ""]
        all_tags.update(tags)

    # Record which XBRL tag each line came from: first place to look when a
    # number ever looks wrong.
    if all_tags:
        parts += ["## XBRL tags used", "", "| Line item | Tag |", "| --- | --- |"]
        for label, tag in sorted(all_tags.items()):
            parts.append(f"| {label} | `{tag}` |")
        parts.append("")
    return "\n".join(parts)
