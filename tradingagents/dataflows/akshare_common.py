"""Common utilities for the akshare data vendor.

Ticker format conversion between A-share 6-digit codes and the
formats that various akshare APIs expect.
"""

import re

# 上交所主板 + 科创板
_SHANGHAI_PREFIXES = ("60", "68")
# 深交所主板 + 创业板
_SHENZHEN_PREFIXES = ("00", "30", "20")


def _strip_suffixes(code: str) -> str:
    """Remove common exchange suffixes like .SS, .SZ, .SH."""
    return re.sub(r"\.(SS|SZ|SH|ss|sz|sh)$", "", code.strip())


def detect_exchange(symbol: str) -> str:
    """Return 'sh' (Shanghai) or 'sz' (Shenzhen) for a 6-digit A-share code."""
    code = symbol.strip().replace(".", "")
    # Already prefixed
    if code.lower().startswith("sh"):
        return "sh"
    if code.lower().startswith("sz"):
        return "sz"
    if not code.isdigit() or len(code) != 6:
        raise ValueError(
            f"Cannot detect exchange for ticker '{symbol}'. "
            "Expected a 6-digit A-share code (e.g. 600519) or exchange-prefixed form (e.g. sh600519)."
        )
    if code.startswith(_SHANGHAI_PREFIXES):
        return "sh"
    if code.startswith(_SHENZHEN_PREFIXES):
        return "sz"
    raise ValueError(
        f"Cannot detect exchange for ticker '{symbol}'. "
        "Prefix does not match known Shanghai (60/68) or Shenzhen (00/30/20) ranges."
    )


def to_raw_code(symbol: str) -> str:
    """Extract the bare 6-digit code from any ticker form.

    Accepts:
      - '600519'          → '600519'
      - 'sh600519'        → '600519'
      - '600519.SS'       → '600519' (Yahoo-style suffix)
      - '000001.SZ'       → '000001'

    This is the preferred format for most akshare EastMoney APIs.
    """
    code = _strip_suffixes(symbol)
    code = code.strip().replace(".", "")
    # Already prefixed → strip
    if code.lower().startswith("sh") and len(code) == 8:
        return code[2:]
    if code.lower().startswith("sz") and len(code) == 8:
        return code[2:]
    # Bare 6-digit
    if code.isdigit() and len(code) == 6:
        return code
    raise ValueError(
        f"Invalid A-share ticker '{symbol}'. "
        "Expected a 6-digit code (e.g. 600519), exchange-prefixed (sh600519), "
        "or suffixed form (600519.SS)."
    )


def normalize_ticker(symbol: str) -> str:
    """Convert any form of A-share ticker to exchange-prefixed format.

    Input:  '600519', 'sh600519', '600519.SS'
    Output: 'sh600519'
    """
    raw = to_raw_code(symbol)
    return detect_exchange(raw) + raw


def validate_ticker(symbol: str) -> str:
    """Validate and normalize ticker, raising ValueError for invalid inputs.

    Also used as a security gate to ensure the symbol is safe for path
    interpolation, consistent with ``safe_ticker_component``.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError(f"ticker must be a non-empty string, got {symbol!r}")
    if len(symbol.strip()) > 16:
        raise ValueError(f"ticker exceeds 16 chars: {symbol!r}")
    # Allow: digits, letters, dot, underscore, dash, caret
    if not re.match(r"^[A-Za-z0-9._\-\^]+$", symbol.strip()):
        raise ValueError(f"ticker contains disallowed characters: {symbol!r}")
    return normalize_ticker(symbol)


def apply_akshare_patches():
    """Monkey-patch akshare to work around an upstream bug.

    akshare's ``_stock_balance_sheet_by_report_ctype_em`` scrapes an HTML
    ``<input id="hidctype">`` from the EastMoney website.  That element no
    longer exists (site restructured), so ``soup.find(...)`` returns
    ``None`` and ``None["value"]`` raises ``TypeError``, breaking all three
    financial-statement endpoints:

      * ``stock_balance_sheet_by_report_em``
      * ``stock_profit_sheet_by_report_em``
      * ``stock_cash_flow_sheet_by_report_em``

    The *company_type* value is just ``"1"`` for standard companies and
    ``"4"`` for financials.  We fall back to ``"1"``, which covers all
    non-bank / non-insurance / non-securities A‑shares.

    This patch is idempotent — calling it repeatedly is safe.
    """
    try:
        from akshare.stock_feature import stock_three_report_em

        _original = stock_three_report_em._stock_balance_sheet_by_report_ctype_em

        if hasattr(_original, "_tradingagents_patched"):
            return

        # Clear any previously cached (broken) results on the original function
        if hasattr(_original, "cache_clear"):
            _original.cache_clear()

        def _patched(symbol: str = "SH600519") -> str:
            try:
                return _original(symbol)
            except TypeError:
                return "1"

        _patched._tradingagents_patched = True
        stock_three_report_em._stock_balance_sheet_by_report_ctype_em = _patched

    except ImportError:
        pass
