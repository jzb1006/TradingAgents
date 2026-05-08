"""akshare-based fundamental data for A-shares.

Covers company overview, balance sheet, cash flow, and income statement.
"""

from datetime import datetime
from typing import Annotated

import pandas as pd

from .akshare_common import to_raw_code, normalize_ticker


_PREFERRED_INFO_KEYS = [
    ("股票简称", "Name"),
    ("行业", "Industry"),
    ("总市值", "Market Cap"),
    ("流通市值", "Circulating Market Cap"),
    ("总股本", "Total Shares"),
    ("流通股", "Circulating Shares"),
    ("市盈率-动态", "PE (TTM)"),
    ("市净率", "PB"),
    ("每股收益", "EPS"),
    ("每股净资产", "Book Value per Share"),
    ("营业收入", "Revenue"),
    ("营业利润", "Operating Profit"),
    ("净利润", "Net Income"),
    ("净利润同比", "Net Income YoY"),
    ("毛利率", "Gross Margin"),
    ("净利率", "Net Margin"),
    ("ROE", "ROE"),
    ("上市时间", "Listed Date"),
    ("总资产", "Total Assets"),
    ("资产负债率", "Debt to Asset Ratio"),
]


def get_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """Retrieve comprehensive fundamental data for an A-share stock via akshare.

    Args:
        ticker: A-share code (e.g. ``600519`` or ``sh600519``).
        curr_date: Current trading date (not used for akshare, present for interface compatibility).

    Returns:
        Formatted string containing key fundamental metrics.
    """
    try:
        import akshare as ak
    except ImportError:
        return "Error: akshare is not installed. Install it with: pip install akshare"

    try:
        code = to_raw_code(ticker)
    except ValueError as e:
        return f"Error: {e}"

    try:
        info_df = ak.stock_individual_info_em(symbol=code)
    except Exception as e:
        return f"Error retrieving fundamentals for {ticker} via akshare: {str(e)}"

    if info_df is None or info_df.empty:
        return f"No fundamentals data found for symbol '{ticker}'"

    info_map = dict(zip(info_df["item"], info_df["value"]))

    header = f"# Company Fundamentals for {ticker.upper()}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    header += f"# Source: akshare (EastMoney)\n\n"

    lines = []
    for cn_name, en_label in _PREFERRED_INFO_KEYS:
        value = info_map.get(cn_name)
        if value is not None and str(value).strip() and str(value).strip() != "-":
            lines.append(f"{en_label}: {value}")

    return header + "\n".join(lines)


def _financial_statement_to_csv(
    df: pd.DataFrame, ticker: str, statement_type: str, freq: str = "quarterly"
) -> str:
    """Convert a financial statement DataFrame to CSV format."""
    if df is None or df.empty:
        return f"No {statement_type} data found for symbol '{ticker}'"

    header = f"# {statement_type} data for {ticker.upper()} ({freq})\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    header += f"# Source: akshare (EastMoney)\n\n"

    return header + df.to_csv(index=False)


def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """Retrieve balance sheet for an A-share stock via akshare."""
    try:
        import akshare as ak
    except ImportError:
        return "Error: akshare is not installed. Install it with: pip install akshare"

    try:
        code = normalize_ticker(ticker)
    except ValueError as e:
        return f"Error: {e}"
    try:
        df = ak.stock_balance_sheet_by_report_em(symbol=code)
    except Exception as e:
        return f"Error retrieving balance sheet for {ticker}: {str(e)}"

    return _financial_statement_to_csv(df, ticker, "Balance Sheet", freq)


def get_cashflow(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """Retrieve cash flow statement for an A-share stock via akshare."""
    try:
        import akshare as ak
    except ImportError:
        return "Error: akshare is not installed. Install it with: pip install akshare"

    try:
        code = normalize_ticker(ticker)
    except ValueError as e:
        return f"Error: {e}"
    try:
        df = ak.stock_cash_flow_sheet_by_report_em(symbol=code)
    except Exception as e:
        return f"Error retrieving cash flow for {ticker}: {str(e)}"

    return _financial_statement_to_csv(df, ticker, "Cash Flow", freq)


def get_income_statement(
    ticker: Annotated[str, "ticker symbol"],
    freq: Annotated[str, "reporting frequency: annual/quarterly"] = "quarterly",
    curr_date: Annotated[str, "current date you are trading at, yyyy-mm-dd"] = None,
) -> str:
    """Retrieve income statement for an A-share stock via akshare."""
    try:
        import akshare as ak
    except ImportError:
        return "Error: akshare is not installed. Install it with: pip install akshare"

    try:
        code = normalize_ticker(ticker)
    except ValueError as e:
        return f"Error: {e}"
    try:
        df = ak.stock_profit_sheet_by_report_em(symbol=code)
    except Exception as e:
        return f"Error retrieving income statement for {ticker}: {str(e)}"

    return _financial_statement_to_csv(df, ticker, "Income Statement", freq)
