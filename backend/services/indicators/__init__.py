# services/indicators/__init__.py
"""
Importing this package registers every built-in indicator (trend, momentum,
volatility, volume, statistical, price_action, candlestick) into
INDICATOR_REGISTRY. Add a new category by writing indicators/<name>.py with
@indicator-decorated functions and importing it below -- nothing else needs
to change.
"""
from .registry import INDICATOR_REGISTRY, indicator, get_categories, list_indicators  # noqa: F401

from . import trend        # noqa: F401
from . import momentum     # noqa: F401
from . import volatility   # noqa: F401
from . import volume       # noqa: F401
from . import statistical  # noqa: F401
from . import price_action  # noqa: F401
from . import candlestick  # noqa: F401