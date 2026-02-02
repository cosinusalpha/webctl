"""
Adblock filtering for webctl.

Provides network-level ad blocking and cosmetic filtering using filter lists
from uBlock Origin and EasyList.
"""

from .engine import AdblockEngine, get_adblock_engine

__all__ = ["AdblockEngine", "get_adblock_engine"]
