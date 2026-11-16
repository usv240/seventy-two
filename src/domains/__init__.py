"""The disease Seventy-Two watches is chosen by the DOMAIN setting (default measles). See measles.py."""
import importlib
import os


AVAILABLE = ("measles", "hepatitis_a")


def load(name=None):
    return importlib.import_module(f"domains.{name or os.environ.get('DOMAIN', 'measles')}")
