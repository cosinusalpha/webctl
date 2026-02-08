"""
Consent-O-Matic integration for cookie banner handling.

This module provides cookie consent banner handling based on the Consent-O-Matic
rule system, supporting 44+ Cookie Management Platforms (CMPs).

See: https://github.com/cavi-au/Consent-O-Matic
"""

from .engine import ConsentOMaticEngine, ConsentOMaticResult

__all__ = ["ConsentOMaticEngine", "ConsentOMaticResult"]
