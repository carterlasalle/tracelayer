"""Vulture whitelist: names referenced dynamically by frameworks or registries.

Vulture treats these as used so dead-code scans don't report false positives.
This module is analyzed, never executed.
"""

from tracelayer.symbols import go, java, javascript, python, rust, typescript

# SymbolParser classes loaded by name via the lazy registry (importlib).
GoParser = go.GoParser
JavaParser = java.JavaParser
JavaScriptParser = javascript.JavaScriptParser
PythonParser = python.PythonParser
RustParser = rust.RustParser
TypeScriptParser = typescript.TypeScriptParser

# Consumed by frameworks rather than our code.
scan = None  # typer option parameter (migrate subcommands)
main = None  # console-script entry point (pyproject [project.scripts])
