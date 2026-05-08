"""akshare-based OHLCV stock data for A-shares."""

from datetime import datetime
from typing import Annotated

import pandas as pd

from .akshare_common import to_raw_code


def get_stock(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Retrieve daily OHLCV data for an A-share stock via akshare.

    Args:
        symbol: A-share ticker code, e.g. ``600519``, ``sh600519``, or ``600519.SS``.
        start_date: Start date in yyyy-mm-dd format.
        end_date: End date in yyyy-mm-dd format.

    Returns:
        CSV string with columns Date, Open, High, Low, Close, Volume.
    """
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    try:
        import akshare as ak
    except ImportError:
        return (
            "Error: akshare is not installed. "
            "Install it with: pip install akshare"
        )

    try:
        ticker = to_raw_code(symbol)
    except ValueError as e:
        return f"Error: {e}"

    try:
        df = ak.stock_zh_a_hist(
            symbol=ticker,
            period="daily",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust="qfq",  # 前复权
        )
    except Exception as e:
        return f"Error fetching stock data for {symbol} via akshare: {str(e)}"

    if df is None or df.empty:
        return (
            f"No data found for symbol '{symbol}' between {start_date} and {end_date}"
        )

    # Normalise column names to the format expected by downstream agents
    col_map = {
        "日期": "Date",
        "开盘": "Open",
        "最高": "High",
        "最低": "Low",
        "收盘": "Close",
        "成交量": "Volume",
    }
    df = df.rename(columns=col_map)

    # Keep only the standardised OHLCV columns
    keep_cols = [c for c in ["Date", "Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[keep_cols]

    # Normalise formatting
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for col in ["Open", "High", "Low", "Close"]:
        if col in df.columns:
            df[col] = df[col].astype(float).round(2)

    # Build header + CSV output matching yfinance / Alpha Vantage format
    header = f"# Stock data for {symbol.upper()} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(df)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    header += f"# Source: akshare (A-share, qfq-adjusted)\n\n"

    return header + df.to_csv(index=False)
