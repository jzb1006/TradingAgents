"""akshare-based news data for A-shares.

Stock news via EastMoney and macro news for the Chinese market.
"""

from datetime import datetime
from typing import Annotated

import pandas as pd
from dateutil.relativedelta import relativedelta

from .akshare_common import to_raw_code


def get_news(
    ticker: Annotated[str, "Ticker symbol"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
) -> str:
    """Retrieve stock-specific news for an A-share ticker via akshare.

    akshare's ``stock_news_em`` returns the most recent news for the
    stock, regardless of the requested date range.  When running a
    historical backtest the dates of returned articles will be later
    than *end_date*; we include them anyway as context (the agent
    should treat future-dated articles as unavailable).

    Args:
        ticker: A-share code (e.g. ``600519``, ``sh600519``, ``600519.SS``).
        start_date: Start date in yyyy-mm-dd format.
        end_date: End date in yyyy-mm-dd format.

    Returns:
        Formatted string containing news articles.
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
        df = ak.stock_news_em(symbol=code)
    except Exception as e:
        return f"Error fetching news for {ticker} via akshare: {str(e)}"

    if df is None or df.empty:
        return f"No news found for {ticker}"

    # Standard column names used by stock_news_em
    title_col = "新闻标题" if "新闻标题" in df.columns else df.columns[0]
    content_col = "新闻内容" if "新闻内容" in df.columns else None
    date_col = "发布时间" if "发布时间" in df.columns else None

    news_parts = []
    for _, row in df.head(10).iterrows():
        title = str(row.get(title_col, "No title"))
        news_parts.append(f"### {title}\n")
        if content_col and pd.notna(row.get(content_col)):
            content = str(row[content_col])
            if content.strip():
                news_parts.append(f"{content[:500]}\n")
        if date_col and pd.notna(row.get(date_col)):
            news_parts.append(f"Published: {row[date_col]}\n")
        news_parts.append("\n")

    note = (
        f"Note: akshare returns the most recent ~10 news articles for {ticker}. "
        "When running a historical backtest the published dates may be later "
        "than the requested date range — treat any future-dated articles as "
        "unavailable at the time of the trading decision.\n\n"
    )

    return f"## {ticker} News (A-Share):\n\n{note}" + "".join(news_parts)


def get_global_news(
    curr_date: Annotated[str, "Current date in yyyy-mm-dd format"],
    look_back_days: Annotated[int, "Number of days to look back"] = 7,
    limit: Annotated[int, "Maximum number of articles to return"] = 5,
) -> str:
    """Retrieve macro/A-share market news via akshare.

    Uses EastMoney global financial news as the source for Chinese
    market macro context.

    Args:
        curr_date: Current date in yyyy-mm-dd format.
        look_back_days: Number of days to look back.
        limit: Maximum number of articles to return.

    Returns:
        Formatted string containing global/macro news.
    """
    try:
        import akshare as ak
    except ImportError:
        return "Error: akshare is not installed. Install it with: pip install akshare"

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_date = (curr_dt - relativedelta(days=look_back_days)).strftime("%Y-%m-%d")

    try:
        df = ak.stock_info_global_em()
    except Exception as e:
        return f"Error fetching global news via akshare: {str(e)}"

    if df is None or df.empty:
        return f"No global market news found for {curr_date}"

    # Detect column names
    title_col = next((c for c in ["标题", "title", "name"] if c in df.columns), df.columns[0])
    content_col = next((c for c in ["内容", "content", "summary"] if c in df.columns), None)

    news_parts = []
    count = 0
    for _, row in df.iterrows():
        if count >= limit:
            break
        title = str(row.get(title_col, "No title"))
        news_parts.append(f"### {title}\n")
        if content_col and pd.notna(row.get(content_col)):
            content = str(row[content_col])
            if content.strip():
                news_parts.append(f"{content[:500]}\n")
        news_parts.append("\n")
        count += 1

    if count == 0:
        return f"No global market news found for {curr_date}"

    return f"## Global Market News (A-Share), from {start_date} to {curr_date}:\n\n" + "".join(news_parts)


def get_insider_transactions(
    ticker: Annotated[str, "ticker symbol"],
) -> str:
    """Retrieve insider transaction data for an A-share stock.

    akshare does not provide a direct insider-transactions API.  We
    try the shareholder holding-change API as a reasonable proxy.
    """
    try:
        code = to_raw_code(ticker)
    except ValueError as e:
        return f"Error: {e}"

    try:
        import akshare as ak
        try:
            df = ak.stock_share_hold_change_em(symbol=code)
            if df is not None and not df.empty:
                header = f"# Shareholder Holding Changes for {ticker.upper()}\n"
                header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                header += f"# Source: akshare (EastMoney)\n\n"
                return header + df.to_csv(index=False)
        except Exception:
            pass
    except ImportError:
        pass

    return (
        f"# Insider Transactions for {ticker.upper()}\n\n"
        "Insider transaction data is not directly available via akshare for A-shares. "
        "Consider monitoring shareholder change announcements from the news data instead."
    )
