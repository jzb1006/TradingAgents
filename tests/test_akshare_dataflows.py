"""Tests for akshare dataflow modules.

Focuses on unit-testable logic: ticker conversion, data format alignment,
and output contract consistency with the yfinance vendor path.
"""

import sys
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from tradingagents.dataflows.akshare_common import (
    normalize_ticker,
    to_raw_code,
    detect_exchange,
    validate_ticker,
)
from tradingagents.dataflows.akshare_stock import get_stock
from tradingagents.dataflows.akshare_news import get_news, get_global_news
from tradingagents.dataflows.akshare_fundamentals import get_fundamentals


class TestTickerNormalization:
    """Unit tests for A-share ticker format conversion."""

    def test_normalize_shanghai_code(self):
        assert normalize_ticker("600519") == "sh600519"
        assert normalize_ticker("688981") == "sh688981"

    def test_normalize_shenzhen_code(self):
        assert normalize_ticker("000001") == "sz000001"
        assert normalize_ticker("300750") == "sz300750"

    def test_normalize_already_prefixed(self):
        assert normalize_ticker("sh600519") == "sh600519"
        assert normalize_ticker("SZ000001") == "sz000001"

    def test_normalize_yahoo_suffix(self):
        """LLMs may try Yahoo-style suffixes like .SS, .SZ."""
        assert normalize_ticker("600519.SS") == "sh600519"
        assert normalize_ticker("000001.SZ") == "sz000001"

    def test_to_raw_code(self):
        """to_raw_code should always yield the bare 6-digit code."""
        assert to_raw_code("600519") == "600519"
        assert to_raw_code("sh600519") == "600519"
        assert to_raw_code("600519.SS") == "600519"
        assert to_raw_code("000001.SZ") == "000001"
        assert to_raw_code("SZ000001") == "000001"

    def test_detect_exchange_shanghai(self):
        assert detect_exchange("600519") == "sh"
        assert detect_exchange("688111") == "sh"

    def test_detect_exchange_shenzhen(self):
        assert detect_exchange("000001") == "sz"
        assert detect_exchange("300750") == "sz"
        assert detect_exchange("200596") == "sz"

    def test_invalid_ticker_raises(self):
        with pytest.raises(ValueError):
            normalize_ticker("123456")  # unknown prefix
        with pytest.raises(ValueError):
            normalize_ticker("AAPL")    # not A-share
        with pytest.raises(ValueError):
            normalize_ticker("")        # empty

    def test_validate_ticker_rejects_dangerous(self):
        with pytest.raises(ValueError):
            validate_ticker("../../../etc/passwd")
        with pytest.raises(ValueError):
            validate_ticker("sh60%00")

    def test_validate_ticker_accepts_valid(self):
        assert validate_ticker("600519") == "sh600519"
        assert validate_ticker("600519.SS") == "sh600519"
        assert validate_ticker("000001.SZ") == "sz000001"


class TestStockData:
    """Tests for akshare stock data format alignment."""

    def _make_fake_hist(self):
        """Build a DataFrame matching akshare's stock_zh_a_hist output."""
        return pd.DataFrame({
            "日期": ["2025-01-02", "2025-01-03", "2025-01-04"],
            "开盘": [10.0, 10.2, 10.1],
            "收盘": [10.5, 10.3, 10.8],
            "最高": [10.8, 10.5, 11.0],
            "最低": [9.9, 10.1, 10.0],
            "成交量": [1000000, 1200000, 980000],
            "成交额": [10500000, 12400000, 10600000],
            "振幅": [9.0, 3.9, 9.8],
            "涨跌幅": [5.0, -1.9, 4.8],
            "涨跌额": [0.5, -0.2, 0.5],
            "换手率": [2.0, 2.4, 1.9],
        })

    def test_get_stock_output_format(self):
        """Output must contain standard OHLCV CSV columns."""
        fake_data = self._make_fake_hist()

        with patch("akshare.stock_zh_a_hist", return_value=fake_data):
            result = get_stock("600519", "2025-01-02", "2025-01-04")

        assert "Date" in result
        assert "Open" in result
        assert "High" in result
        assert "Low" in result
        assert "Close" in result
        assert "Volume" in result
        # Chinese column names should NOT appear
        assert "开盘" not in result
        assert "收盘" not in result

    def test_get_stock_csv_parseable(self):
        """Output must be parseable CSV with the expected columns."""
        fake_data = self._make_fake_hist()

        with patch("akshare.stock_zh_a_hist", return_value=fake_data):
            result = get_stock("600519", "2025-01-02", "2025-01-04")

        # Extract CSV portion (after comment lines, skip blank lines)
        lines = result.strip().split("\n")
        csv_lines = [l for l in lines if l and not l.startswith("#")]
        assert len(csv_lines) >= 2  # header + at least one data row

        header = csv_lines[0].split(",")
        for col in ["Date", "Open", "High", "Low", "Close", "Volume"]:
            assert col in header, f"Column '{col}' missing from output: {header}"

    def test_get_stock_empty_data(self):
        """Graceful message when no data is available."""
        with patch("akshare.stock_zh_a_hist", return_value=pd.DataFrame()):
            result = get_stock("000001", "2020-01-01", "2020-01-05")

        assert "No data found" in result


class TestNewsData:
    """Tests for akshare news output."""

    def _make_fake_news(self):
        return pd.DataFrame({
            "新闻标题": ["Test news 1", "Test news 2"],
            "新闻内容": ["Content 1", "Content 2"],
            "发布时间": ["2025-01-03 10:00:00", "2025-01-04 11:00:00"],
        })

    def test_get_news_output(self):
        fake_data = self._make_fake_news()
        with patch("akshare.stock_news_em", return_value=fake_data):
            result = get_news("600519", "2025-01-01", "2025-01-05")
        assert "Test news 1" in result
        assert "Content 1" in result
        assert "Published:" in result

    def test_get_news_no_data(self):
        with patch("akshare.stock_news_em", return_value=pd.DataFrame()):
            result = get_news("600519", "2025-01-01", "2025-01-05")
        assert "No news found" in result


class TestFundamentals:
    """Tests for akshare fundamentals output."""

    def _make_fake_info(self):
        return pd.DataFrame({
            "item": [
                "股票简称", "行业", "总市值", "市盈率-动态", "市净率", "ROE",
                "营业收入", "净利润", "每股收益",
            ],
            "value": [
                "贵州茅台", "白酒", "2000000000000", "25.5", "8.2", "28.0",
                "120000000000", "60000000000", "50.5",
            ],
        })

    def test_get_fundamentals_output(self):
        fake_data = self._make_fake_info()
        with patch("akshare.stock_individual_info_em", return_value=fake_data):
            result = get_fundamentals("600519")

        assert "Name: 贵州茅台" in result
        assert "Industry: 白酒" in result
        assert "PE (TTM): 25.5" in result
        assert "ROE: 28.0" in result

    def test_get_fundamentals_empty(self):
        with patch("akshare.stock_individual_info_em", return_value=pd.DataFrame()):
            result = get_fundamentals("000001")
        assert "No fundamentals data found" in result


class TestModuleImports:
    """Verify all akshare modules are importable and registered."""

    def test_akshare_vendor_registered(self):
        from tradingagents.dataflows.interface import VENDOR_LIST, VENDOR_METHODS
        assert "akshare" in VENDOR_LIST

        for method, vendors in VENDOR_METHODS.items():
            assert "akshare" in vendors, f"akshare missing from VENDOR_METHODS['{method}']"

    def test_akshare_aggregator_exports(self):
        from tradingagents.dataflows import akshare
        for name in (
            "get_stock", "get_indicator",
            "get_fundamentals", "get_balance_sheet",
            "get_cashflow", "get_income_statement",
            "get_news", "get_global_news", "get_insider_transactions",
        ):
            assert hasattr(akshare, name), f"akshare module missing '{name}'"


class TestConfigIntegration:
    """Verify default_config shows akshare as an option."""

    def test_default_config_shows_akshare_option(self):
        from tradingagents.default_config import DEFAULT_CONFIG
        for category_vendor in DEFAULT_CONFIG["data_vendors"].values():
            # The comment in config says "Options: alpha_vantage, yfinance, akshare"
            # Verify the value itself is valid
            assert category_vendor in ("yfinance", "alpha_vantage", "akshare")
