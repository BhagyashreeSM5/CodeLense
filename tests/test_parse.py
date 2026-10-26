from app.parse import extract_python_symbols

SAMPLE = """import os
from pathlib import Path

def load(path):
    return Path(path).read_text()

class Parser:
    def parse(self, text):
        return text

async def fetch():
    pass
"""


def test_extracts_functions_classes_methods_and_imports():
    symbols = {(s.name, s.kind): s for s in extract_python_symbols(SAMPLE)}
    assert symbols[("load", "function")].start_line == 4
    assert symbols[("Parser", "class")].start_line == 7
    assert symbols[("parse", "method")].start_line == 8
    assert symbols[("fetch", "function")].start_line == 11
    assert ("os", "import") in symbols
    assert ("pathlib", "import") in symbols


def test_class_end_line_recorded():
    symbols = {s.name: s for s in extract_python_symbols(SAMPLE)}
    assert symbols["Parser"].end_line == 9
