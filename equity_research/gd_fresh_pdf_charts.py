"""Vector charts for the dated GD research report."""

from math import ceil, floor, log10

from .gd_pdf import BLUE, BODY, GOLD, INK, MARGIN, MUTED, NAVY, PALE, RULE


CASE_ORDER = ("bear", "base", "bull")
CASE_COLORS = {"bear": MUTED, "base": BLUE, "bull": GOLD}
CASE_DASHES = {"bear": (2, 3), "base": (), "bull": (6, 2)}


def _scale(values):
    low, high = min(values), max(values)
    target_step = (high - low) / 4 or max(abs(high) / 20, 1)
    magnitude = 10 ** floor(log10(target_step))
    step = next(multiple * magnitude for multiple in (1, 2, 2.5, 5, 10) if multiple * magnitude >= target_step)
    first, last = floor(low / step), ceil(high / step)
    if first == last:
        first, last = first - 1, last + 1
    return [index * step for index in range(first, last + 1)]


def target_range_chart(page, prices: dict[str, float], reference_price: float) -> None:
    """Plot the three 12-month targets against the dated reference close."""
    prices = {case.casefold(): price for case, price in prices.items()}
    ticks = _scale([*prices.values(), reference_price])
    page.heading("12-month targets by operating case")
    canvas = page.canvas
    left, right = MARGIN + 70, MARGIN + BODY - 84
    top, bottom = page.y - 28, page.y - 89

    def position(price):
        return left + (right - left) * (price - ticks[0]) / (ticks[-1] - ticks[0])

    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.55)
    for index in range(len(CASE_ORDER)):
        y = top - index * 23
        canvas.line(left, y, right, y)
    canvas.line(left, bottom, right, bottom)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    for tick in ticks:
        x = position(tick)
        canvas.line(x, bottom, x, bottom - 3)
        canvas.drawCentredString(x, bottom - 13, f"${tick:,.0f}")

    close_x = position(reference_price)
    canvas.setStrokeColor(NAVY)
    canvas.setDash(2, 3)
    canvas.setLineWidth(0.8)
    canvas.line(close_x, bottom, close_x, top + 8)
    canvas.setDash()
    canvas.setFillColor(NAVY)
    canvas.setFont("Helvetica-Bold", 8.5)
    canvas.drawCentredString(close_x, top + 14, f"Close ${reference_price:,.2f}")

    for index, case in enumerate(CASE_ORDER):
        price = prices[case]
        y = top - index * 23
        canvas.setFillColor(CASE_COLORS[case])
        canvas.circle(position(price), y, 4, stroke=0, fill=1)
        canvas.setFillColor(INK)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(MARGIN, y - 3, case.title())
        canvas.drawRightString(MARGIN + BODY, y - 3, f"${price:,.2f}")
    canvas.restoreState()
    page.y = bottom - 19
    page.paragraph("USD per share. The dashed line marks the reference close.", small=True, gap=5)


def revenue_chart(
    page,
    actuals: tuple[tuple[int, float], ...],
    scenarios: dict[str, tuple[tuple[int, float], ...]],
    active_case: str,
) -> None:
    """Plot reported revenue and the three annual forecast paths in USD billions."""
    scenarios = {case.casefold(): periods for case, periods in scenarios.items()}
    active_case = active_case.casefold()
    years = sorted({year for year, _ in actuals} | {year for periods in scenarios.values() for year, _ in periods})
    ticks = _scale([value for _, value in actuals] + [value for periods in scenarios.values() for _, value in periods])
    page.heading("Revenue by operating case")
    canvas = page.canvas
    left, right = MARGIN + 32, MARGIN + BODY - 106
    top, bottom = page.y - 24, page.y - 117

    def x_value(year):
        return left + (right - left) * (year - years[0]) / (years[-1] - years[0])

    def y_value(value):
        return bottom + (top - bottom) * (value - ticks[0]) / (ticks[-1] - ticks[0])

    def line(periods, color, width, dash=()):
        path = canvas.beginPath()
        year, value = periods[0]
        path.moveTo(x_value(year), y_value(value))
        for year, value in periods[1:]:
            path.lineTo(x_value(year), y_value(value))
        canvas.setStrokeColor(color)
        canvas.setLineWidth(width)
        canvas.setDash(dash)
        canvas.drawPath(path)
        canvas.setDash()

    canvas.saveState()
    first_forecast = min(periods[0][0] for periods in scenarios.values())
    shading_start = (x_value(actuals[-1][0]) + x_value(first_forecast)) / 2
    canvas.setFillColor(PALE)
    canvas.rect(shading_start, bottom, right - shading_start, top - bottom, stroke=0, fill=1)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(left, top + 8, "Reported")
    canvas.drawString(shading_start + 6, top + 8, "Forecast")

    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    for tick in ticks:
        y = y_value(tick)
        canvas.line(left, y, right, y)
        canvas.drawRightString(left - 7, y - 2.5, f"{tick:g}")
    for year in years:
        canvas.drawCentredString(x_value(year), bottom - 14, str(year))

    line(actuals, NAVY, 1.8)
    for case in CASE_ORDER:
        line((actuals[-1], *scenarios[case]), CASE_COLORS[case], 2.2 if case == active_case else 1.3,
             CASE_DASHES[case])

    endpoints = sorted(
        ((y_value(scenarios[case][-1][1]), case) for case in CASE_ORDER),
        reverse=True,
    )
    label_y = top + 13
    for endpoint, case in endpoints:
        year, value = scenarios[case][-1]
        label_y = min(max(endpoint, bottom), label_y - 13)
        canvas.setStrokeColor(CASE_COLORS[case])
        canvas.setFillColor(CASE_COLORS[case])
        canvas.setLineWidth(0.8)
        canvas.circle(x_value(year), endpoint, 2.5, stroke=0, fill=1)
        canvas.line(x_value(year) + 4, endpoint, right + 11, label_y)
        canvas.setFillColor(INK)
        canvas.setFont("Helvetica-Bold" if case == active_case else "Helvetica", 8.5)
        canvas.drawString(right + 16, label_y - 3, f"{case.title()} {value:,.2f}")
    canvas.restoreState()
    page.y = bottom - 21
    page.paragraph("USD billions. The heavier forecast line shows the selected operating case.", small=True, gap=5)
