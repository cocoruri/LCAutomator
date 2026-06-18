"""lcu_watch's implementation package.

Kept as an explicit (non-namespace) package so imports resolve the same way
from source, from a different working directory, and from the PyInstaller build
(which statically follows `from src import ...` to decide what to bundle).
"""
