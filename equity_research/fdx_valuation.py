"""Dated FedEx portfolio valuation with independently calculated Excel controls."""

from dataclasses import dataclass, field
from datetime import date
from math import isclose, isfinite
from statistics import median

from .gd_replay import CachedWorkbook
from .valuation import FcfeDiscount, FcfeStub, ForecastCashFlow, discount_fcfe_with_stub


AS_OF = date(2025, 10, 22)
HORIZON = date(2026, 10, 22)
QUOTE_DATE = date(2025, 10, 21)
QUOTE_SOURCE = "https://chartexchange.com/symbol/nyse-fdx/historical/"
EXCEL_EPOCH = date(1899, 12, 30)
FORECAST_COLUMNS = "CDEFG"
DATE_FORMAT = "mmm d, yyyy"
PRICE_FORMAT = '"$"#,##0.00'
PERCENT_FORMAT = "0.00%"
AMOUNT_FORMAT = "#,##0.00"
MULTIPLE_FORMAT = '0.00"x"'

CellValue = str | int | float
Cell = tuple[CellValue, str | None, str | None]


@dataclass(frozen=True)
class FDXPeer:
    name: str
    ticker: str
    price: float
    shares_millions: float
    debt_millions: float
    cash_millions: float
    forward_eps: float
    forward_ebitda_millions: float

    @property
    def market_cap_millions(self) -> float:
        return self.price * self.shares_millions

    @property
    def enterprise_value_millions(self) -> float:
        return self.market_cap_millions + self.debt_millions - self.cash_millions

    @property
    def price_to_earnings(self) -> float:
        return self.price / self.forward_eps

    @property
    def ev_to_ebitda(self) -> float:
        return self.enterprise_value_millions / self.forward_ebitda_millions


@dataclass(frozen=True)
class FDXComparables:
    peers: tuple[FDXPeer, ...]
    subject: FDXPeer
    median_price_to_earnings: float
    median_ev_to_ebitda: float
    implied_price_to_earnings_usd: float
    implied_ev_to_ebitda_usd: float


@dataclass(frozen=True)
class FDXValuation:
    as_of: date
    horizon_date: date
    quote_date: date
    quote_price: float
    quote_source: str
    cost_of_equity: float
    terminal_growth: float
    spot_shares_millions: float
    horizon_shares_millions: float
    normalized_terminal_fcfe_millions: float
    spot: FcfeDiscount
    horizon: FcfeDiscount
    spot_price_return: float
    horizon_price_return: float
    cost_of_equity_axis: tuple[float, ...]
    terminal_growth_axis: tuple[float, ...]
    spot_sensitivity: tuple[tuple[float, ...], ...]
    horizon_sensitivity: tuple[tuple[float, ...], ...]
    comps: FDXComparables
    cells: dict[str, dict[str, Cell]]
    expected: dict[str, dict[str, CellValue]]
    limitations: tuple[str, ...]


@dataclass
class _Sheet:
    cells: dict[str, Cell] = field(default_factory=dict)
    expected: dict[str, CellValue] = field(default_factory=dict)

    def put(
        self, address: str, value: CellValue, kind: str | None = None, fmt: str | None = None,
        *, expected: CellValue | None = None,
    ) -> None:
        self.cells[address] = (value, kind, fmt)
        if expected is not None:
            self.expected[address] = expected


def _date_serial(value: date) -> int:
    return (value - EXCEL_EPOCH).days


def _archived_comps(saved: CachedWorkbook) -> FDXComparables:
    source = "Comps NF"
    observations = []
    for row in range(6, 11):
        name = saved.value(source, f"A{row}")
        ticker = saved.value(source, f"B{row}")
        if not isinstance(name, str) or not name or not isinstance(ticker, str) or not ticker:
            raise ValueError(f"Missing company identity in {source} row {row}.")
        earnings_row = row + 9
        peer = FDXPeer(
            name, ticker,
            saved.number(source, f"C{row}"), saved.number(source, f"E{row}"),
            saved.number(source, f"G{row}"), saved.number(source, f"H{row}"),
            saved.number(source, f"H{earnings_row}"), saved.number(source, f"O{earnings_row}"),
        )
        if min(peer.price, peer.shares_millions, peer.forward_eps, peer.forward_ebitda_millions) <= 0:
            raise ValueError(f"Nonpositive share or earnings input for {ticker} in {source}.")
        observations.append(peer)

    *peers, subject = observations
    if subject.ticker != "FDX" or tuple(peer.ticker for peer in peers) != ("UPS", "JBHT.O", "GXO", "CHRW.O"):
        raise ValueError("The archived FedEx peer and subject rows do not match the valuation mapping.")
    median_pe = median(peer.price_to_earnings for peer in peers)
    median_ev = median(peer.ev_to_ebitda for peer in peers)
    implied_pe = median_pe * subject.forward_eps
    implied_ev = (median_ev * subject.forward_ebitda_millions - subject.debt_millions
                  + subject.cash_millions) / subject.shares_millions
    for address, amount in (("H31", median_pe), ("O31", median_ev), ("H34", implied_pe), ("O34", implied_ev)):
        if not isclose(amount, saved.number(source, address), rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError(f"Recalculated comparable value disagrees with {source}!{address}.")
    return FDXComparables(tuple(peers), subject, median_pe, median_ev, implied_pe, implied_ev)


def _comps_sheet(comps: FDXComparables) -> _Sheet:
    sheet = _Sheet()
    sheet.put("A1", "FedEx | Archived trading comparables", "title")
    sheet.put("A2", "USD millions except per-share figures and multiples. Source: Comps NF workbook snapshot.", "note")
    sheet.put("A3", "Market and I/B/E/S estimates have no verified observation timestamps.", "note")
    for column, label in zip("ABCEFGHI", ("Company", "Ticker", "Price", "Shares (m)", "Market cap", "Debt", "Cash", "EV"), strict=True):
        sheet.put(f"{column}5", label, "header")
    sheet.put("A12", "First forward fiscal-year estimates", "section")
    for address, label in (("A14", "Company"), ("B14", "Ticker"), ("H14", "FYR1 EPS"), ("O14", "FYR1 EBITDA")):
        sheet.put(address, label, "header")
    sheet.put("A21", "First forward fiscal-year multiples", "section")
    for address, label in (("A23", "Company"), ("B23", "Ticker"), ("H23", "P/E"), ("O23", "EV/EBITDA")):
        sheet.put(address, label, "header")

    for row, peer in enumerate((*comps.peers, comps.subject), start=6):
        estimates, multiples = row + 9, row + 18
        sheet.put(f"A{row}", peer.name)
        sheet.put(f"B{row}", peer.ticker)
        for column, value in (("C", peer.price), ("E", peer.shares_millions), ("G", peer.debt_millions), ("H", peer.cash_millions)):
            sheet.put(f"{column}{row}", value, "input", PRICE_FORMAT if column == "C" else AMOUNT_FORMAT)
        sheet.put(f"F{row}", f"=C{row}*E{row}", fmt=AMOUNT_FORMAT, expected=peer.market_cap_millions)
        sheet.put(f"I{row}", f"=F{row}+G{row}-H{row}", fmt=AMOUNT_FORMAT, expected=peer.enterprise_value_millions)
        for target_row in (estimates, multiples):
            sheet.put(f"A{target_row}", f"=A{row}", expected=peer.name)
            sheet.put(f"B{target_row}", f"=B{row}", expected=peer.ticker)
        sheet.put(f"H{estimates}", peer.forward_eps, "input", PRICE_FORMAT)
        sheet.put(f"O{estimates}", peer.forward_ebitda_millions, "input", AMOUNT_FORMAT)
        sheet.put(f"H{multiples}", f"=C{row}/H{estimates}", fmt=MULTIPLE_FORMAT, expected=peer.price_to_earnings)
        sheet.put(f"O{multiples}", f"=I{row}/O{estimates}", fmt=MULTIPLE_FORMAT, expected=peer.ev_to_ebitda)

    sheet.put("A31", "Peer median", "total")
    sheet.put("H31", "=MEDIAN(H24:H27)", "total", MULTIPLE_FORMAT, expected=comps.median_price_to_earnings)
    sheet.put("O31", "=MEDIAN(O24:O27)", "total", MULTIPLE_FORMAT, expected=comps.median_ev_to_ebitda)
    sheet.put("A34", "Implied FedEx value / share", "total")
    sheet.put("H34", "=H31*H19", "total", PRICE_FORMAT, expected=comps.implied_price_to_earnings_usd)
    sheet.put("O34", "=(O31*O19-G10+H10)/E10", "total", PRICE_FORMAT, expected=comps.implied_ev_to_ebitda_usd)
    sheet.put("A35", "Peer values use the archived estimate snapshot and stand separately from the FCFE valuation.", "note")
    return sheet


def _discount(
    flows: tuple[ForecastCashFlow, ...], cutoff: date, rate: float, growth: float,
    shares: float, normalized_fcfe: float,
) -> FcfeDiscount:
    remaining = tuple(flow for flow in flows if flow.period_end > cutoff)
    first = remaining[0]
    fraction = (first.period_end - cutoff).days / 365
    stub = FcfeStub(cutoff, first.period_end, first.fcfe_millions * fraction)
    return discount_fcfe_with_stub(
        stub, remaining[1:], cutoff, rate, growth, shares,
        terminal_fcfe_millions=normalized_fcfe * (1 + growth),
    )


def _period_grid(
    sheet: _Sheet, flows: tuple[ForecastCashFlow, ...], result: FcfeDiscount, cutoff: date, horizon: bool,
) -> None:
    date_row, years_row, cash_row, pv_row = (38, 39, 40, 41) if horizon else (14, 15, 16, 17)
    anchor = "G4" if horizon else "G3"
    sheet.put(f"A{years_row}", "ACT/365 discount years")
    sheet.put(f"A{cash_row}", "FCFE after target date (USDm)" if horizon else "Annual FCFE (USDm)")
    sheet.put(f"A{pv_row}", "Present value of remaining FCFE (USDm)", "total")
    by_end = {period.period_end: period for period in result.periods}

    for column, flow in zip(FORECAST_COLUMNS, flows, strict=True):
        year = f"{column}{years_row}"
        period = by_end.get(flow.period_end)
        years = max((flow.period_end - cutoff).days / 365, 0.0)
        if horizon:
            date_cell = f"={column}14"
            cash = f"={column}16*MIN({year},1)"
            amount = period.fcfe_millions if period else 0.0
            numerator = f"{column}{cash_row}"
        else:
            date_cell = _date_serial(flow.period_end)
            cash = f"='Derived cash flow'!{column}59"
            amount = flow.fcfe_millions
            numerator = f"{column}{cash_row}*MIN({year},1)"
        sheet.put(f"{column}{date_row}", date_cell, "header", DATE_FORMAT, expected=_date_serial(flow.period_end))
        sheet.put(year, f"=MAX(({column}{date_row}-${anchor[0]}${anchor[1:]})/365,0)", fmt="0.0000", expected=years)
        sheet.put(f"{column}{cash_row}", cash, fmt=AMOUNT_FORMAT, expected=amount)
        sheet.put(f"{column}{pv_row}", f"={numerator}/(1+$B$3)^{year}", fmt=AMOUNT_FORMAT,
                  expected=period.present_value_millions if period else 0.0)


def _sensitivity(
    sheet: _Sheet, *, header: int, spot: bool, rates: tuple[float, ...], growths: tuple[float, ...],
    grid: tuple[tuple[float, ...], ...], base_value: float,
) -> None:
    years_row, cash_row, shares = (15, 16, "$B$4") if spot else (39, 40, "$I$4")
    sheet.put(f"B{header}", "=B24" if spot else "=B46", "total", PRICE_FORMAT, expected=base_value)
    sheet.put(f"C{header - 1}", "Terminal growth", "section")
    for offset, (column, growth) in enumerate(zip("CDEFGHI", growths, strict=True), start=-3):
        sheet.put(f"{column}{header}", f"=$B$19+({offset})*$L$26", "header", PERCENT_FORMAT, expected=growth)
    for offset, (rate, values) in enumerate(zip(rates, grid, strict=True)):
        row = header + 1 + offset
        rate_ref = f"$B{row}"
        sheet.put(f"B{row}", f"=$B$3+({3 - offset})*$L$27", "header", PERCENT_FORMAT, expected=rate)
        terms = []
        for column in FORECAST_COLUMNS:
            fraction = f"*MIN(${column}${years_row},1)" if spot else ""
            terms.append(f"${column}${cash_row}{fraction}/(1+{rate_ref})^${column}${years_row}")
        for column, value in zip("CDEFGHI", values, strict=True):
            growth_ref = f"{column}${header}"
            terminal = f"$E$22*(1+{growth_ref})/({rate_ref}-{growth_ref})/(1+{rate_ref})^$G${years_row}"
            formula = f'=IF({rate_ref}<={growth_ref},"n.a.",(SUM({",".join(terms)})+{terminal})/{shares})'
            sheet.put(f"{column}{row}", formula, "total" if offset == 3 and column == "F" else None,
                      PRICE_FORMAT, expected=value)
    sheet.put(f"A{header + 4}", "Cost of equity")


def _dcf_sheet(data, flows, terminal_adjustments, rate_step, growth_step):
    sheet = _Sheet()
    sheet.put("A1", "FedEx | Archived FCFE valuation", "title")
    sheet.put("A2", "USD millions except per-share figures and ratios", "note")
    for address, label in (("A3", "Cost of equity"), ("A4", "Reference shares (millions)"), ("A6", "Reference close"),
                           ("H3", "Valuation cutoff"), ("H4", "12-month target date"), ("A19", "Terminal growth")):
        sheet.put(address, label)
    sheet.put("B3", data["rate"], "input", PERCENT_FORMAT, expected=data["rate"])
    sheet.put("D3", "Archived Ke!D38. Individual market observations are undated.", "note")
    sheet.put("B4", "=I3", fmt=AMOUNT_FORMAT, expected=data["spot_shares"])
    sheet.put("G3", _date_serial(AS_OF), "actual", DATE_FORMAT, expected=_date_serial(AS_OF))
    sheet.put("G4", "=DATE(YEAR(G3)+1,MONTH(G3),DAY(G3))", fmt=DATE_FORMAT, expected=_date_serial(HORIZON))
    sheet.put("I3", "='Balance sheet'!F169", fmt=AMOUNT_FORMAT, expected=data["spot_shares"])
    sheet.put("I4", "='Balance sheet'!G169", fmt=AMOUNT_FORMAT, expected=data["target_shares"])
    sheet.put("B6", data["price"], "actual", PRICE_FORMAT, expected=data["price"])
    sheet.put("C6", _date_serial(QUOTE_DATE), "actual", DATE_FORMAT, expected=_date_serial(QUOTE_DATE))
    sheet.put("D6", QUOTE_SOURCE, "note")
    sheet.put("A7", "The preceding regular-session close is used for the October 22 pre-open cutoff.", "note")
    sheet.put("B19", data["growth"], "input", PERCENT_FORMAT, expected=data["growth"])
    sheet.put("L26", growth_step, "input", PERCENT_FORMAT)
    sheet.put("L27", rate_step, "input", PERCENT_FORMAT)
    sheet.put("K26", "Growth grid step")
    sheet.put("K27", "Equity discount grid step")

    for row, label, formula, expected in (
        (19, "FY2030 net borrowing", "='Debt schedule'!K9-'Debt schedule'!K8", terminal_adjustments[0]),
        (20, "FY2030 D&A", "='Income statement'!L10", terminal_adjustments[1]),
        (21, "FY2030 capex", "=CapEx!J15", terminal_adjustments[2]),
        (22, "Normalized FY2030 FCFE", "=G16-E19-MAX(E20-E21,0)", data["normalized"]),
    ):
        sheet.put(f"D{row}", label)
        sheet.put(f"E{row}", formula, fmt=AMOUNT_FORMAT, expected=expected)

    for horizon, result in ((False, data["spot"]), (True, data["target"])):
        cutoff = HORIZON if horizon else AS_OF
        _period_grid(sheet, flows, result, cutoff, horizon)
        top, terminal_row, target_row, summary = (36, 43, 46, 9) if horizon else (12, 21, 24, 8)
        years_row, pv_row, shares = (39, 41, "I4") if horizon else (15, 17, "B4")
        title = "12-month FCFE value" if horizon else "Spot FCFE value"
        sheet.put(f"A{top}", title, "section")
        for row, label, formula, fmt, expected in (
            (terminal_row, "Terminal value at May 31, 2030", "=$E$22*(1+$B$19)/($B$3-$B$19)", AMOUNT_FORMAT, result.terminal_value_millions),
            (terminal_row + 1, "Present value of terminal FCFE", f"=B{terminal_row}/(1+$B$3)^G{years_row}", AMOUNT_FORMAT, result.terminal_present_value_millions),
            (terminal_row + 2, "Equity present value", f"=SUM(C{pv_row}:G{pv_row})+B{terminal_row + 1}", AMOUNT_FORMAT, result.equity_present_value_millions),
            (target_row, "Equity value / share", f"=B{terminal_row + 2}/{shares}", PRICE_FORMAT, result.value_per_share),
            (summary, f"{title} / share", f"=B{target_row}", PRICE_FORMAT, result.value_per_share),
        ):
            sheet.put(f"A{row}", label, "total" if row in (target_row, summary) else None)
            sheet.put(f"B{row}", formula, "total" if row in (target_row, summary) else None, fmt, expected=expected)
        sheet.put(f"G{summary}", "12-month price return" if horizon else "Spot price difference")
        sheet.put(f"I{summary}", f"=B{summary}/$B$6-1", fmt=PERCENT_FORMAT, expected=result.value_per_share / data["price"] - 1)

    for header, key, grid in ((26, "spot", data["spot_grid"]), (48, "target", data["target_grid"])):
        _sensitivity(sheet, header=header, spot=key == "spot", rates=data["rates"], growths=data["growths"],
                     grid=grid, base_value=data[key].value_per_share)
    sheet.put("A57", "The DCF applies no opening cash credit. The 12-month denominator uses forecast FY2026 closing shares.", "note")
    return sheet


def build_fdx_valuation(
    saved: CachedWorkbook,
    *,
    fcfe_millions: tuple[float, ...],
    terminal_net_borrowing_millions: float,
    terminal_da_millions: float,
    terminal_capex_millions: float,
    horizon_shares_millions: float,
) -> FDXValuation:
    """Build editable DCF and comps sheets and compute their controls independently."""
    if len(fcfe_millions) != len(FORECAST_COLUMNS) or any(not isfinite(value) for value in fcfe_millions):
        raise ValueError("FedEx valuation requires five finite annual FCFE estimates for FY2026–FY2030.")
    adjustments = (terminal_net_borrowing_millions, terminal_da_millions, terminal_capex_millions)
    if any(not isfinite(value) for value in adjustments) or min(adjustments[1:]) < 0:
        raise ValueError("FedEx terminal cash adjustments require finite borrowing, D&A and capex, with nonnegative D&A and capex.")
    flows = tuple(ForecastCashFlow(saved.day("DCF", f"{column}14"), cash)
                  for column, cash in zip(FORECAST_COLUMNS, fcfe_millions, strict=True))
    if tuple(flow.period_end for flow in flows) != tuple(date(year, 5, 31) for year in range(2026, 2031)):
        raise ValueError("FedEx archival valuation requires fiscal year-ends of May 31, 2026 through 2030.")

    price = saved.number("DCF", "B6")
    if price != 242.84:
        raise ValueError("DCF!B6 does not match the October 21, 2025 reference close of $242.84.")
    rate = saved.number("Ke", "D38")
    growth = saved.number("DCF", "B19")
    spot_shares = saved.number("Balance sheet", "F169")
    normalized = fcfe_millions[-1] - terminal_net_borrowing_millions - max(terminal_da_millions - terminal_capex_millions, 0)
    if normalized <= 0:
        raise ValueError("FedEx Gordon-growth valuation requires positive normalized FY2030 FCFE.")
    rate_step, growth_step = saved.number("DCF", "L27"), saved.number("DCF", "L26")
    if rate_step <= 0 or growth_step <= 0:
        raise ValueError("FedEx valuation sensitivity steps must be positive.")
    rates = tuple(rate + offset * rate_step for offset in range(3, -4, -1))
    growths = tuple(growth + offset * growth_step for offset in range(-3, 4))

    def calculate(cutoff, shares):
        base = _discount(flows, cutoff, rate, growth, shares, normalized)
        grid = tuple(tuple(_discount(flows, cutoff, row_rate, col_growth, shares, normalized).value_per_share
                           for col_growth in growths) for row_rate in rates)
        return base, grid

    spot, spot_grid = calculate(AS_OF, spot_shares)
    target, target_grid = calculate(HORIZON, horizon_shares_millions)
    comps = _archived_comps(saved)
    dcf = _dcf_sheet({
        "price": price, "rate": rate, "growth": growth, "normalized": normalized,
        "spot_shares": spot_shares, "target_shares": horizon_shares_millions,
        "spot": spot, "target": target, "spot_grid": spot_grid, "target_grid": target_grid,
        "rates": rates, "growths": growths,
    }, flows, adjustments, rate_step, growth_step)
    peers = _comps_sheet(comps)
    return FDXValuation(
        as_of=AS_OF,
        horizon_date=HORIZON,
        quote_date=QUOTE_DATE,
        quote_price=price,
        quote_source=QUOTE_SOURCE,
        cost_of_equity=rate,
        terminal_growth=growth,
        spot_shares_millions=spot_shares,
        horizon_shares_millions=horizon_shares_millions,
        normalized_terminal_fcfe_millions=normalized,
        spot=spot,
        horizon=target,
        spot_price_return=spot.value_per_share / price - 1,
        horizon_price_return=target.value_per_share / price - 1,
        cost_of_equity_axis=rates,
        terminal_growth_axis=growths,
        spot_sensitivity=spot_grid,
        horizon_sensitivity=target_grid,
        comps=comps,
        cells={"DCF": dcf.cells, "Comps": peers.cells},
        expected={"DCF": dcf.expected, "Comps": peers.expected},
        limitations=(
            "The valuation freezes the October 22, 2025 pre-open cutoff. The archived $242.84 quote is the October 21 close.",
            "The archived forecast includes FedEx Freight and uses the legacy May fiscal year-end.",
            "The cost of equity and peer consensus values use archived vendor-era inputs with unverified observation dates.",
            "Normalized terminal FCFE removes forecast net borrowing and increases reinvestment when capex falls below D&A.",
            "The DCF applies no opening cash credit. The target uses forecast FY2026 ending shares as a proxy.",
            "The return fields measure archived modeled price change.",
        ),
    )
