"""akshare-based technical-indicator data for A-shares.

Computes indicators from OHLCV data using stockstats (same engine as the
yfinance path), so output format is identical across vendors.
"""

import os
from datetime import datetime
from typing import Annotated

import pandas as pd
from dateutil.relativedelta import relativedelta
from stockstats import wrap

from .akshare_common import to_raw_code, validate_ticker
from .config import get_config
from .utils import safe_ticker_component


# Indicator descriptions shared with the yfinance vendor path
_INDICATOR_DESCRIPTIONS = {
    "close_50_sma": (
        "50 SMA: A medium-term trend indicator. "
        "Usage: Identify trend direction and serve as dynamic support/resistance. "
        "Tips: It lags price; combine with faster indicators for timely signals."
    ),
    "close_200_sma": (
        "200 SMA: A long-term trend benchmark. "
        "Usage: Confirm overall market trend and identify golden/death cross setups. "
        "Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries."
    ),
    "close_10_ema": (
        "10 EMA: A responsive short-term average. "
        "Usage: Capture quick shifts in momentum and potential entry points. "
        "Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals."
    ),
    "macd": (
        "MACD: Computes momentum via differences of EMAs. "
        "Usage: Look for crossovers and divergence as signals of trend changes. "
        "Tips: Confirm with other indicators in low-volatility or sideways markets."
    ),
    "macds": (
        "MACD Signal: An EMA smoothing of the MACD line. "
        "Usage: Use crossovers with the MACD line to trigger trades. "
        "Tips: Should be part of a broader strategy to avoid false positives."
    ),
    "macdh": (
        "MACD Histogram: Shows the gap between the MACD line and its signal. "
        "Usage: Visualize momentum strength and spot divergence early. "
        "Tips: Can be volatile; complement with additional filters in fast-moving markets."
    ),
    "rsi": (
        "RSI: Measures momentum to flag overbought/oversold conditions. "
        "Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. "
        "Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis."
    ),
    "boll": (
        "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. "
        "Usage: Acts as a dynamic benchmark for price movement. "
        "Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals."
    ),
    "boll_ub": (
        "Bollinger Upper Band: Typically 2 standard deviations above the middle line. "
        "Usage: Signals potential overbought conditions and breakout zones. "
        "Tips: Confirm signals with other tools; prices may ride the band in strong trends."
    ),
    "boll_lb": (
        "Bollinger Lower Band: Typically 2 standard deviations below the middle line. "
        "Usage: Indicates potential oversold conditions. "
        "Tips: Use additional analysis to avoid false reversal signals."
    ),
    "atr": (
        "ATR: Averages true range to measure volatility. "
        "Usage: Set stop-loss levels and adjust position sizes based on current market volatility. "
        "Tips: It's a reactive measure, so use it as part of a broader risk management strategy."
    ),
    "vwma": (
        "VWMA: A moving average weighted by volume. "
        "Usage: Confirm trends by integrating price action with volume data. "
        "Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses."
    ),
    "mfi": (
        "MFI: The Money Flow Index is a momentum indicator that uses both price and volume to measure buying and selling pressure. "
        "Usage: Identify overbought (>80) or oversold (<20) conditions and confirm the strength of trends or reversals. "
        "Tips: Use alongside RSI or MACD to confirm signals; divergence between price and MFI can indicate potential reversals."
    ),
}

_SUPPORTED_INDICATORS = set(_INDICATOR_DESCRIPTIONS.keys())


def _load_ohlcv_akshare(symbol: str, curr_date: str) -> pd.DataFrame:
    """Fetch OHLCV data via akshare with local caching.

    Downloads 5 years of data up to today and caches per symbol. Rows after
    curr_date are filtered out to prevent look-ahead bias.
    """
    try:
        import akshare as ak
    except ImportError:
        raise RuntimeError("akshare is not installed. Install it with: pip install akshare")

    safe_symbol = safe_ticker_component(symbol)
    try:
        ticker = to_raw_code(symbol)
    except ValueError:
        raise RuntimeError(f"Invalid A-share ticker: {symbol}")
    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    today = pd.Timestamp.today()
    start = today - pd.DateOffset(years=5)
    start_str = start.strftime("%Y-%m-%d")
    end_str = today.strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    data_file = os.path.join(
        config["data_cache_dir"],
        f"{safe_symbol}-akshare-data-{start_str}-{end_str}.csv",
    )

    if os.path.exists(data_file):
        data = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
    else:
        try:
            data = ak.stock_zh_a_hist(
                symbol=ticker,
                period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=today.strftime("%Y%m%d"),
                adjust="qfq",
            )
        except Exception as e:
            raise RuntimeError(f"Failed to fetch data for {symbol} via akshare: {e}")

        if data is None or data.empty:
            raise RuntimeError(f"No data returned for {symbol} via akshare")

        col_map = {
            "日期": "Date",
            "开盘": "Open",
            "最高": "High",
            "最低": "Low",
            "收盘": "Close",
            "成交量": "Volume",
        }
        data = data.rename(columns=col_map)
        keep_cols = [c for c in ["Date", "Open", "High", "Low", "Close", "Volume"] if c in data.columns]
        data = data[keep_cols]
        data.to_csv(data_file, index=False, encoding="utf-8")

    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"])
    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    data = data.dropna(subset=["Close"])
    data[price_cols] = data[price_cols].ffill().bfill()

    # Prevent look-ahead bias
    data = data[data["Date"] <= curr_date_dt]

    return data


def get_indicator(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator to get the analysis and report of"],
    curr_date: Annotated[str, "The current trading date you are trading on, YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"] = 30,
) -> str:
    """Retrieve technical-indicator values for an A-share stock.

    Computed from akshare OHLCV data via stockstats, with output format
    identical to the yfinance vendor path.
    """
    if indicator not in _SUPPORTED_INDICATORS:
        raise ValueError(
            f"Indicator {indicator} is not supported. "
            f"Please choose from: {sorted(_SUPPORTED_INDICATORS)}"
        )

    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_date_dt - relativedelta(days=look_back_days)

    try:
        data = _load_ohlcv_akshare(symbol, curr_date)
        df = wrap(data)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

        # Trigger stockstats to calculate the indicator
        df[indicator]

        result_dict = {}
        for _, row in df.iterrows():
            date_str = row["Date"]
            val = row[indicator]
            result_dict[date_str] = "N/A" if pd.isna(val) else str(val)

        # Walk backwards from curr_date to before, collecting values
        current_dt = curr_date_dt
        date_values = []
        while current_dt >= before:
            date_str = current_dt.strftime("%Y-%m-%d")
            value = result_dict.get(date_str, "N/A: Not a trading day (weekend or holiday)")
            date_values.append((date_str, value))
            current_dt -= relativedelta(days=1)

        ind_string = "\n".join(f"{d}: {v}" for d, v in date_values)

    except Exception as e:
        return f"Error getting indicator data for {indicator} on {curr_date}: {e}"

    return (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {curr_date}:\n\n"
        + ind_string
        + "\n\n"
        + _INDICATOR_DESCRIPTIONS.get(indicator, "No description available.")
    )
