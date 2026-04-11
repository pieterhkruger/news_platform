"""
Public package API for Daily Indaba seeding.

Only the orchestration types are exported here. Internal helper functions stay
in their own modules so the package boundary is explicit: callers should depend
on the high-level seeding service, not on the package's private implementation
details.
"""

from .demo_news import DemoNewsSeeder, SeedDependencies, SeedSummary

__all__ = [
    "DemoNewsSeeder",
    "SeedDependencies",
    "SeedSummary",
]
