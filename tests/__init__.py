"""Tests for the Fantasy Premier League integration.

This file is load-bearing, not boilerplate. pytest's default ``importmode=prepend``
picks each test module's basedir by walking up while ``__init__.py`` files exist.
Without this file the basedir is ``tests/``, modules import as top-level names,
and ``from .conftest import ...`` fails with "attempted relative import with no
known parent package" — while the repo root never reaches ``sys.path``, so
``import custom_components.fantasy_pl`` fails too. With it, the walk stops at the
repo root and both resolve.
"""
