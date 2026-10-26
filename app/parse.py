"""Extract symbols (functions, classes, methods, imports) and module structure from source code."""
import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Symbol:
    name: str
    kind: str
    start_line: int
    end_line: int
    docstring: str | None = None
    called_by: list[str] = field(default_factory=list)


@dataclass
class ImportItem:
    module: str | None
    names: list[str]  # Imported symbol/module names
    level: int = 0    # 0 for absolute, >0 for relative (. or ..)
    line: int = 1


@dataclass
class ParsedModule:
    path: str
    docstring: str | None
    symbols: list[Symbol]
    imports: list[ImportItem]
    referenced_names: set[str]
    has_main_block: bool = False


class ReferenceVisitor(ast.NodeVisitor):
    def __init__(self):
        self.names: set[str] = set()
        self.has_main_block = False

    def visit_Name(self, node: ast.Name):
        self.names.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute):
        self.names.add(node.attr)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare):
        names = [n.id for n in ast.walk(node) if isinstance(n, ast.Name)]
        constants = [c.value for c in ast.walk(node) if isinstance(c, ast.Constant)]
        if "__name__" in names and "__main__" in constants:
            self.has_main_block = True
        self.generic_visit(node)


def _definition(node, kind: str) -> Symbol:
    doc = ast.get_docstring(node)
    return Symbol(node.name, kind, node.lineno, node.end_lineno, docstring=doc)


def extract_python_symbols(source: str) -> list[Symbol]:
    """Raises SyntaxError if the source cannot be parsed."""
    tree = ast.parse(source)
    symbols: list[Symbol] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_definition(node, "function"))
        elif isinstance(node, ast.ClassDef):
            symbols.append(_definition(node, "class"))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(_definition(child, "method"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                symbols.append(Symbol(alias.name, "import", node.lineno, node.end_lineno))
        elif isinstance(node, ast.ImportFrom):
            symbols.append(Symbol(node.module or "", "import", node.lineno, node.end_lineno))
    return symbols


def parse_python_module(rel_path: str, source: str) -> ParsedModule:
    """Parse Python source into symbols, docstrings, imports, and references."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ParsedModule(
            path=rel_path,
            docstring=None,
            symbols=[],
            imports=[],
            referenced_names=set(),
            has_main_block=False,
        )

    docstring = ast.get_docstring(tree)
    symbols: list[Symbol] = []
    imports: list[ImportItem] = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_definition(node, "function"))
        elif isinstance(node, ast.ClassDef):
            symbols.append(_definition(node, "class"))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(_definition(child, "method"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                symbols.append(Symbol(alias.name, "import", node.lineno, node.end_lineno))
                imports.append(
                    ImportItem(
                        module=alias.name,
                        names=[alias.asname or alias.name],
                        level=0,
                        line=node.lineno,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            symbols.append(Symbol(node.module or "", "import", node.lineno, node.end_lineno))
            imported_names = [alias.name for alias in node.names]
            imports.append(
                ImportItem(
                    module=node.module,
                    names=imported_names,
                    level=node.level,
                    line=node.lineno,
                )
            )

    visitor = ReferenceVisitor()
    visitor.visit(tree)

    return ParsedModule(
        path=rel_path,
        docstring=docstring,
        symbols=symbols,
        imports=imports,
        referenced_names=visitor.names,
        has_main_block=visitor.has_main_block,
    )


def parse_file(path: Path) -> list[Symbol]:
    if path.suffix != ".py":
        return []
    source = path.read_text(encoding="utf-8", errors="replace")
    return extract_python_symbols(source)

