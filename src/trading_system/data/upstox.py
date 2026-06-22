"""Direct Upstox HTTP client used by the deterministic core.

The Cursor-side ``upstox-optionchain`` MCP wraps the same endpoints. We call
HTTP directly here so the Python program is self-contained and can run as a
daemon (cron/systemd) without an LLM in the loop. The agent-side flow can
still use the MCP for ad-hoc analysis.

Authentication: reads ``UPSTOX_ACCESS_TOKEN`` from environment. The token
issued for the MCP can be reused.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

UPSTOX_BASE = "https://api.upstox.com"
TOKEN_ENV = "UPSTOX_ACCESS_TOKEN"


def _quote_rfc3986(string: str, safe: str = "", encoding: str | None = None, errors: str | None = None) -> str:
    """Encode using %20 for spaces (RFC 3986) instead of + (form-encoding).
    Commas are kept literal since Upstox uses them to separate instrument keys.
    """
    return quote(string, safe=safe + ",")


def _parse_interval(interval: str) -> tuple[str, str]:
    """Convert shorthand like ``'5minute'`` into V3 path segments ``('minutes', '5')``."""

    m = re.match(r"^(\d+)(minute|hour|day|week|month)s?$", interval)
    if m:
        return m.group(2) + "s", m.group(1)
    if interval in ("1d", "day"):
        return "days", "1"
    if interval in ("1w", "week"):
        return "weeks", "1"
    if interval in ("1M", "month"):
        return "months", "1"
    raise ValueError(f"Unsupported interval format: {interval!r}")


class UpstoxError(RuntimeError):
    """Raised on non-200 responses or transport errors."""


def _headers() -> Dict[str, str]:
    token = os.environ.get(TOKEN_ENV)
    if not token:
        raise UpstoxError(
            f"{TOKEN_ENV} not set. Export the same token used by the upstox-optionchain MCP."
        )
    return {"Accept": "application/json", "Authorization": f"Bearer {token}"}


_RETRY = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
)


class UpstoxClient:
    """Thin wrapper around the Upstox REST endpoints we actually use."""

    def __init__(self, base_url: str = UPSTOX_BASE, timeout: float = 10.0):
        self.base_url = base_url
        self.timeout = timeout

    @_RETRY
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            qs = urlencode(params, quote_via=_quote_rfc3986)
            url = f"{url}?{qs}"
        with httpx.Client(timeout=self.timeout, headers=_headers()) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.json()

    def option_chain(
        self,
        instrument_key: str,
        expiry_date: Optional[str] = None,
        expiry_weekday: str = "Tuesday",
        expiry_cadence: str = "weekly",
    ) -> Dict[str, Any]:
        """``/v2/option/chain`` — returns full chain for the given expiry (YYYY-MM-DD)."""

        if expiry_date is None:
            if expiry_cadence == "monthly":
                expiry_date = next_monthly_expiry(weekday=expiry_weekday).isoformat()
            else:
                expiry_date = next_weekly_expiry(weekday=expiry_weekday).isoformat()
        params = {"instrument_key": instrument_key, "expiry_date": expiry_date}
        return self._get("/v2/option/chain", params=params)

    def ltp(self, instrument_keys: List[str]) -> Dict[str, Any]:
        params = {"instrument_key": ",".join(instrument_keys)}
        return self._get("/v2/market-quote/ltp", params=params)

    def ohlc(self, instrument_keys: List[str], interval: str = "1d") -> Dict[str, Any]:
        params = {"instrument_key": ",".join(instrument_keys), "interval": interval}
        return self._get("/v2/market-quote/ohlc", params=params)

    def full_quote(self, instrument_keys: List[str]) -> Dict[str, Any]:
        """Full quote includes depth (bid/ask 5 levels) when available."""

        params = {"instrument_key": ",".join(instrument_keys)}
        return self._get("/v2/market-quote/quotes", params=params)

    def intraday_candles(self, instrument_key: str, interval: str = "5minute") -> Dict[str, Any]:
        """V3 intraday candles: supports minutes/1-300, hours/1-5, days/1."""

        unit, num = _parse_interval(interval)
        encoded_key = quote(instrument_key, safe="")
        path = f"/v3/historical-candle/intraday/{encoded_key}/{unit}/{num}"
        return self._get(path)

    def historical_candles(
        self,
        instrument_key: str,
        interval: str,
        to_date: str,
        from_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """V3 historical candles: supports minutes, hours, days, weeks, months."""

        unit, num = _parse_interval(interval)
        encoded_key = quote(instrument_key, safe="")
        path = f"/v3/historical-candle/{encoded_key}/{unit}/{num}/{to_date}"
        if from_date:
            path = f"{path}/{from_date}"
        return self._get(path)


_WEEKDAY_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def next_weekly_expiry(today: Optional[date] = None, weekday: str = "Tuesday") -> date:
    """Next ``weekday`` on or after ``today`` (NSE weekly index option expiry).

    ``weekday`` is case-insensitive (e.g. "Tuesday", "Wednesday").
    NIFTY weekly expires on Tuesday.
    """

    today = today or date.today()
    target = _WEEKDAY_MAP[weekday.lower()]
    days_ahead = (target - today.weekday()) % 7
    if days_ahead == 0 and datetime.now().time().hour >= 16:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def next_monthly_expiry(today: Optional[date] = None, weekday: str = "Tuesday") -> date:
    """Last ``weekday`` of the current (or next) month — used for monthly expiries.

    BANKNIFTY monthly options expire on the last Tuesday of the month.
    If that date has already passed (or it's expiry day after 16:00 IST),
    returns the last ``weekday`` of the following month.
    """

    today = today or date.today()
    target = _WEEKDAY_MAP[weekday.lower()]

    def _last_weekday_of_month(year: int, month: int) -> date:
        if month == 12:
            last_day = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = date(year, month + 1, 1) - timedelta(days=1)
        offset = (last_day.weekday() - target) % 7
        return last_day - timedelta(days=offset)

    expiry = _last_weekday_of_month(today.year, today.month)
    past_expiry = expiry < today or (
        expiry == today and datetime.now().time().hour >= 16
    )
    if past_expiry:
        if today.month == 12:
            expiry = _last_weekday_of_month(today.year + 1, 1)
        else:
            expiry = _last_weekday_of_month(today.year, today.month + 1)
    return expiry
