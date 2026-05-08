"""akshare data vendor for the A-share (China) market.

Re-exports all data-access functions from specialised sub-modules so
``interface.py`` can import from a single module.
"""

from .akshare_common import apply_akshare_patches
from .akshare_stock import get_stock
from .akshare_indicator import get_indicator
from .akshare_fundamentals import (
    get_fundamentals,
    get_balance_sheet,
    get_cashflow,
    get_income_statement,
)
from .akshare_news import (
    get_news,
    get_global_news,
    get_insider_transactions,
)

# Apply upstream bug workaround on first import
apply_akshare_patches()
