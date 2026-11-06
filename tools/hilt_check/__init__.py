"""``hilt_check`` — the tier-3 static-analysis surface for authored HILT suites.

Tier 3 is skippable by design. The runtime IoC wrappers (tier 1) and the import-time
``__init_subclass__`` checks (tier 2) guard verdict integrity and hardware safety; this
package guards readability and idiom. Nothing that protects hardware or a verdict may
live only here, so a suite that never runs ``hilt-check`` loses lint feedback and
nothing else.

Public surface:

- :class:`Rule` — the contract every rule implements.
- :class:`RuleRegistry` — discovers the rules under :mod:`hilt_check.rules`.
- :class:`SuiteChecker` — runs a registry over files and directories.
- :class:`SuiteModule`, :class:`Violation`, :class:`Report` — the data the rules and
  the CLI exchange.
"""

from hilt_check.checker import Report, SuiteChecker
from hilt_check.model import Rule, SuiteModule, Violation
from hilt_check.registry import RuleRegistry

__all__ = [
    "Report",
    "Rule",
    "RuleRegistry",
    "SuiteChecker",
    "SuiteModule",
    "Violation",
]
