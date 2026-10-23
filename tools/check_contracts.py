"""Linter for function contracts and test coverage labels.

Usage::

    python tools/check_contracts.py <dir> [--tests <tests dir>] [--check-covers]

Rules (see CLAUDE.md):
    * Python: every function and method (nested ones too, except ``test_*``)
      needs a docstring with ``Raises:``; ``Args:`` when it has parameters
      other than self/cls; ``Returns:`` unless annotated ``-> None``.
    * C++ (.hpp/.cpp/.h/.cc): every function definition/declaration needs a
      preceding ``/** ... */`` block with ``@brief``, ``@throws``, ``@param``
      for each named parameter and ``@return`` for non-void functions.
    * Go: every ``func`` needs a preceding ``//`` comment containing
      ``Args:``, ``Returns:`` and ``Errors:`` lines (Test*/Benchmark*/Fuzz*/
      Example*/main/init are exempt).
    * ``--check-covers``: every function name found in the sources must occur
      in a ``covers: name`` label somewhere under the tests directory.

Output is one ``path:line: message`` per violation; exit code 1 on any
violation, 0 otherwise, 2 on usage errors.

Known limitations (C++ and Go are checked heuristically, without a parser):
    * C++: function-looking declarations at namespace/class scope are found by
      tracking braces on comment/string-masked text.  Variable declarations
      written like calls (``Foo x(1);``) are reported as functions; use
      ``// NOLINT(contract)`` on the line.  ALL_CAPS names are treated as
      macros and ignored, as are ``= default`` / ``= delete`` functions, and
      ``friend`` declarations.  Lambdas and function bodies are not analysed.
      Operator overloads and conversion operators are handled approximately.
    * Go: only top-level ``func`` lines starting in column 0 are seen;
      function literals are ignored; the comment must directly precede
      the ``func`` line.
    * Covers: nested Python functions are not required to be covered
      (they cannot be tested directly); label tokens are matched by the last
      segment after ``::`` or ``.``; C++ constructors are named after the class.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import NamedTuple, Sequence, TextIO

SKIP_DIRS = {"__pycache__", ".git", ".venv", ".pytest_cache", "node_modules", "build"}
CPP_SUFFIXES = (".hpp", ".cpp", ".h", ".cc")
CONTROL_WORDS = {
    "if", "for", "while", "switch", "return", "sizeof", "catch", "decltype", "static_assert",
    "alignas", "alignof", "noexcept", "throw", "new", "delete", "using", "typedef", "else",
    "co_return", "__attribute__", "defined",
}
SPECIFIERS = {
    "static", "inline", "virtual", "constexpr", "consteval", "constinit", "explicit",
    "friend", "extern", "typename", "nodiscard", "maybe_unused",
}
TYPE_WORDS = {
    "int", "char", "short", "long", "unsigned", "signed", "float", "double", "bool", "void",
    "auto", "wchar_t", "size_t",
}
QUALIFIER_WORDS = {"const", "volatile", "struct", "class", "enum", "union", "typename", "unsigned", "signed"}
GO_EXEMPT = ("Test", "Benchmark", "Fuzz", "Example")
TAIL_RE = re.compile(
    r"^(?:\s|const\b|volatile\b|noexcept(?:\s*\([^)]*\))?|override\b|final\b|&&|&|->[^;{=:]+|\[\[[^\]]*\]\])*"
    r"(?:=\s*(?:default|delete|0)\s*)?(?::.*)?$",
    re.S,
)
COVERS_RE = re.compile(r"covers:[ \t]*([^\n]*)")
MACRO_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")


class Violation(NamedTuple):
    """A single contract or coverage problem."""

    path: str
    line: int
    message: str


class CppFunction(NamedTuple):
    """A function declaration or definition found in C++ source."""

    name: str
    qualifier: str
    head: str
    tail: str
    params: list
    start: int
    name_offset: int
    end: int
    skipped: bool


class FuncRef(NamedTuple):
    """A function name with its location, used for the coverage check."""

    name: str
    line: int


def format_violation(violation: Violation) -> str:
    """Render a violation as ``path:line: message``.

    Args:
        violation: Violation to render.

    Returns:
        One-line string without trailing newline.

    Raises:
        None.
    """
    return f"{violation.path}:{violation.line}: {violation.message}"


def has_section(doc: str, name: str) -> bool:
    """Tell whether a docstring contains a ``Name:`` section header line.

    Args:
        doc: Docstring text.
        name: Section name without the colon, e.g. ``"Args"``.

    Returns:
        True if some line starts (after indentation) with ``name:``.

    Raises:
        None.
    """
    return re.search(rf"^\s*{re.escape(name)}:", doc, re.M) is not None


def python_functions(node: ast.AST, nested: bool = False) -> list:
    """Collect all function definitions below an AST node.

    Args:
        node: Root node to search, usually a module.
        nested: True when ``node`` is inside a function body.

    Returns:
        List of ``(function_node, is_nested)`` pairs in source order; methods
        are not nested, functions defined inside functions are.

    Raises:
        None.
    """
    found = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append((child, nested))
            found.extend(python_functions(child, True))
        else:
            found.extend(python_functions(child, nested))
    return found


def check_python_source(text: str, path: str) -> list:
    """Check Python source for missing function contracts.

    Args:
        text: Python source code.
        path: Path used in the reported violations.

    Returns:
        List of Violation, empty when the source is clean.  Unparsable source
        yields a single "syntax error" violation.

    Raises:
        None.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return [Violation(path, error.lineno or 1, f"syntax error: {error.msg}")]
    violations = []
    for node, _nested in python_functions(tree):
        if node.name.startswith("test_"):
            continue
        doc = ast.get_docstring(node)
        if doc is None:
            violations.append(Violation(path, node.lineno, f"{node.name}: missing contract docstring"))
            continue
        arguments = node.args
        names = [a.arg for a in arguments.posonlyargs + arguments.args + arguments.kwonlyargs]
        if arguments.vararg:
            names.append(arguments.vararg.arg)
        if arguments.kwarg:
            names.append(arguments.kwarg.arg)
        has_params = any(name not in ("self", "cls") for name in names)
        returns_none = isinstance(node.returns, ast.Constant) and node.returns.value is None
        if has_params and not has_section(doc, "Args"):
            violations.append(Violation(path, node.lineno, f"{node.name}: docstring lacks 'Args:'"))
        if not returns_none and not has_section(doc, "Returns"):
            violations.append(Violation(path, node.lineno, f"{node.name}: docstring lacks 'Returns:'"))
        if not has_section(doc, "Raises"):
            violations.append(Violation(path, node.lineno, f"{node.name}: docstring lacks 'Raises:'"))
    return violations


def python_function_refs(text: str) -> list:
    """List top-level functions and methods of Python source for coverage.

    Args:
        text: Python source code.

    Returns:
        FuncRef list without nested functions and without ``test_*`` names;
        empty for unparsable source.

    Raises:
        None.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    return [
        FuncRef(node.name, node.lineno)
        for node, nested in python_functions(tree)
        if not nested and not node.name.startswith("test_")
    ]


def mask_cpp(text: str) -> str:
    """Blank out comments, string/char literals and preprocessor lines.

    Args:
        text: C++ source code.

    Returns:
        String of the same length where masked characters are spaces and all
        newlines are preserved, so offsets and line numbers still match.

    Raises:
        None.
    """
    out = list(text)
    n = len(text)
    i = 0
    line_start = True
    while i < n:
        c = text[i]
        if c == "\n":
            line_start = True
            i += 1
            continue
        if line_start and c in " \t":
            i += 1
            continue
        if line_start and c == "#":
            while i < n and not (text[i] == "\n" and text[i - 1] != "\\"):
                if text[i] != "\n":
                    out[i] = " "
                i += 1
            continue
        line_start = False
        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            end = n if end == -1 else end + 2
            for k in range(i, end):
                if text[k] != "\n":
                    out[k] = " "
            i = end
        elif c == '"' and i > 0 and text[i - 1] == "R" and text.find("(", i) != -1:
            open_paren = text.find("(", i)
            closing = ")" + text[i + 1:open_paren] + '"'
            end = text.find(closing, open_paren)
            end = n if end == -1 else end + len(closing)
            for k in range(i, end):
                if text[k] != "\n":
                    out[k] = " "
            i = end
        elif c in "\"'" and not (c == "'" and i > 0 and text[i - 1].isalnum()):
            k = i + 1
            while k < n and text[k] != c and text[k] != "\n":
                k += 2 if text[k] == "\\" else 1
            for m in range(i, min(k + 1, n)):
                if text[m] != "\n":
                    out[m] = " "
            i = k + 1
        else:
            i += 1
    return "".join(out)


def find_doc_comment_before(text: str, offset: int) -> str | None:
    """Return the ``/** ... */`` block that directly precedes an offset.

    Args:
        text: Original (unmasked) C++ source.
        offset: Index in ``text`` where the declaration starts.

    Returns:
        The comment text, or None if the only thing before the offset (apart
        from whitespace) is not a ``/** */`` block.

    Raises:
        None.
    """
    end = offset
    while end > 0 and text[end - 1].isspace():
        end -= 1
    if not text[:end].endswith("*/"):
        return None
    start = text.rfind("/*", 0, end - 1)
    if start == -1 or not text.startswith("/**", start):
        return None
    return text[start:end]


def split_params(params: str) -> list:
    """Split a C++ parameter list on top-level commas.

    Args:
        params: Text between the parentheses of a function declarator.

    Returns:
        Stripped parameter strings; empty for ``""`` and for ``"void"``.

    Raises:
        None.
    """
    parts = []
    depth = 0
    current = ""
    for ch in params:
        if ch in "(<[{":
            depth += 1
        elif ch in ")>]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += ch
    parts.append(current.strip())
    parts = [p for p in parts if p]
    return [] if parts == ["void"] else parts


def param_name(param: str) -> str | None:
    """Extract the declared name from a single C++ parameter.

    Args:
        param: One parameter, e.g. ``"const std::string& s = {}"``.

    Returns:
        The parameter name, or None for unnamed parameters (``int``,
        ``const Foo&``) and ``...``.

    Raises:
        None.
    """
    depth = 0
    for index, ch in enumerate(param):
        if ch in "(<[{":
            depth += 1
        elif ch in ")>]}":
            depth -= 1
        elif ch == "=" and depth == 0:
            param = param[:index]
            break
    param = param.strip()
    pointer = re.search(r"\(\s*[*&]\s*(\w+)\s*\)", param)
    if pointer:
        return pointer.group(1)
    param = re.sub(r"\[[^\]]*\]\s*$", "", param).strip()
    match = re.search(r"([A-Za-z_]\w*)\s*$", param)
    if match is None:
        return None
    name = match.group(1)
    before = param[:match.start()].strip()
    if not before or name in TYPE_WORDS or before.endswith(("::", "<")):
        return None
    if all(word in QUALIFIER_WORDS for word in before.split()):
        return None
    return name


def _strip_template_prefix(stmt: str) -> str:
    """Remove a leading ``template <...>`` clause (balanced angle brackets).

    Args:
        stmt: Statement text, possibly starting with whitespace.

    Returns:
        The statement without the template clause, left-stripped.

    Raises:
        None.
    """
    stmt = stmt.lstrip()
    while re.match(r"template\s*<", stmt):
        depth = 0
        for index in range(stmt.index("<"), len(stmt)):
            depth += stmt[index] == "<"
            depth -= stmt[index] == ">"
            if depth == 0:
                stmt = stmt[index + 1:].lstrip()
                break
        else:
            break
    return stmt


def parse_signature(stmt: str, base: int) -> CppFunction | None:
    """Try to read a statement as a C++ function declaration/definition.

    Args:
        stmt: Masked statement text up to (not including) ``;`` or ``{``.
        base: Offset of ``stmt[0]`` in the whole file.

    Returns:
        CppFunction (``end`` is 0 until the caller sets it), or None when the
        statement does not look like a function.

    Raises:
        None.
    """
    access = re.match(r"\s*(?:public|private|protected)\s*:", stmt)
    skip = access.end() if access else 0
    body = stmt[skip:]
    lead = len(body) - len(body.lstrip())
    start = base + skip + lead
    body = body.strip()
    if not body or re.match(r"(?:class|struct|union|namespace|enum)\b", _strip_template_prefix(body)):
        return None
    paren = body.find("(")
    if paren != -1:
        pre = body[:paren]
        double = re.match(r"\(\s*\)\s*\(", body[paren:]) if re.search(r"operator\s*$", pre) else None
        if double:
            paren += double.end() - 1
        elif pre.count("<") > pre.count(">") and "operator" not in pre:
            return None
    if paren == -1:
        return None
    pre = body[:paren]
    name_re = re.compile(
        r"(?P<qual>(?:[A-Za-z_]\w*(?:<[^<>]*>)?\s*::\s*)*)"
        r"(?P<name>~?[A-Za-z_]\w*|operator\s*(?:\(\s*\)|\[\s*\]|[^\s\w(]+|\w+(?:\s+\w+)*))\s*$"
    )
    match = name_re.search(pre)
    if match is None:
        return None
    name = match.group("name")
    name = re.sub(r"\s+", " ", name) if name.startswith("operator") else name
    head = pre[:match.start()]
    if name in CONTROL_WORDS or "=" in head or re.search(r"\b(?:using|typedef|return|friend)\b", head):
        return None
    if head.count("<") != head.count(">") or MACRO_RE.match(name):
        return None
    depth = 0
    close = -1
    for index in range(paren, len(body)):
        depth += body[index] == "("
        depth -= body[index] == ")"
        if depth == 0:
            close = index
            break
    if close == -1:
        return None
    tail = body[close + 1:]
    if not TAIL_RE.match(tail):
        return None
    params = split_params(body[paren + 1:close])
    skipped = re.search(r"=\s*(?:default|delete)\s*$", tail.split(":")[0].strip()) is not None
    qualifier = re.sub(r"\s+", "", match.group("qual"))
    name_offset = start + match.start("name")
    return CppFunction(name, qualifier, head, tail, params, start, name_offset, 0, skipped)


def _match_brace(code: str, index: int) -> int:
    """Find the ``}`` matching the ``{`` at ``index``.

    Args:
        code: Masked source.
        index: Position of an opening brace.

    Returns:
        Index of the matching closing brace, or ``len(code) - 1`` if unbalanced.

    Raises:
        None.
    """
    depth = 0
    for position in range(index, len(code)):
        depth += code[position] == "{"
        depth -= code[position] == "}"
        if depth == 0:
            return position
    return len(code) - 1


def parse_cpp_functions(text: str) -> list:
    """Find function declarations and definitions in C++ source.

    Args:
        text: C++ source code.

    Returns:
        List of CppFunction in source order.  Function bodies are skipped;
        namespace/class/struct scopes are entered.

    Raises:
        None.
    """
    code = mask_cpp(text)
    found = []
    start = 0
    i = 0
    n = len(code)
    while i < n:
        ch = code[i]
        stmt = code[start:i]
        if ch in ";{" and stmt.count("(") > stmt.count(")"):
            if ch == "{":
                i = _match_brace(code, i)
            i += 1
            continue
        if ch == ";":
            info = parse_signature(stmt, start)
            if info is not None:
                found.append(info._replace(end=i))
            start = i + 1
        elif ch == "{":
            info = parse_signature(stmt, start)
            if info is not None:
                found.append(info._replace(end=i))
                i = _match_brace(code, i)
                start = i + 1
            elif re.search(r"\benum\b", stmt) or not re.search(
                r"(?:^|\s)(?:class|struct|union|namespace|extern)\b", _strip_template_prefix(stmt)
            ):
                i = _match_brace(code, i)
                start = i + 1
            else:
                start = i + 1
        elif ch == "}":
            start = i + 1
        i += 1
    return found


def cpp_returns_value(func: CppFunction) -> bool:
    """Decide whether a C++ function needs an ``@return`` tag.

    Args:
        func: Parsed function.

    Returns:
        False for void functions, constructors and destructors; True otherwise.

    Raises:
        None.
    """
    if re.search(r"->\s*void\b", func.tail):
        return False
    head = _strip_template_prefix(func.head)
    head = re.sub(r"\[\[[^\]]*\]\]", " ", head)
    words = [w for w in re.split(r"\s+", head.strip()) if w and w not in SPECIFIERS]
    text = " ".join(words)
    if func.name.startswith("~"):
        return False
    if func.name.startswith("operator") and not text:
        return re.match(r"operator\s*\w", func.name) is not None
    if not text:
        return False
    if func.qualifier and func.qualifier.rstrip(":").split("::")[-1] == func.name:
        return False
    return re.fullmatch(r"(?:const\s+)?void", text) is None


def check_cpp_source(text: str, path: str) -> list:
    """Check C++ source for missing Doxygen function contracts.

    Args:
        text: C++ source code.
        path: Path used in the reported violations.

    Returns:
        List of Violation, empty when all functions have complete contracts.

    Raises:
        None.
    """
    violations = []
    for func in parse_cpp_functions(text):
        line_end = text.find("\n", func.end)
        line_end = len(text) if line_end == -1 else line_end
        if func.skipped or "NOLINT(contract)" in text[func.start:line_end]:
            continue
        line = text.count("\n", 0, func.name_offset) + 1
        doc = find_doc_comment_before(text, func.start)
        label = func.qualifier + func.name
        if doc is None:
            violations.append(Violation(path, line, f"{label}: missing Doxygen contract comment"))
            continue
        if "@brief" not in doc:
            violations.append(Violation(path, line, f"{label}: contract lacks @brief"))
        for param in func.params:
            name = param_name(param)
            if name and re.search(rf"@param(?:\[[^\]]*\])?\s+{re.escape(name)}\b", doc) is None:
                violations.append(Violation(path, line, f"{label}: contract lacks @param {name}"))
        if cpp_returns_value(func) and "@return" not in doc:
            violations.append(Violation(path, line, f"{label}: contract lacks @return"))
        if "@throws" not in doc:
            violations.append(Violation(path, line, f"{label}: contract lacks @throws"))
    return violations


def cpp_function_refs(text: str) -> list:
    """List C++ functions of a source file for the coverage check.

    Args:
        text: C++ source code.

    Returns:
        FuncRef list with unqualified names (constructors use the class name).

    Raises:
        None.
    """
    return [
        FuncRef(func.name, text.count("\n", 0, func.name_offset) + 1)
        for func in parse_cpp_functions(text)
        if not func.skipped
    ]


def go_functions(text: str) -> list:
    """Find top-level Go functions together with their preceding comment.

    Args:
        text: Go source code.

    Returns:
        List of ``(name, line, comment_lines)``; ``comment_lines`` holds the
        text of the directly preceding ``//`` lines, ``//`` removed.

    Raises:
        None.
    """
    lines = text.split("\n")
    found = []
    for index, line in enumerate(lines):
        match = re.match(r"func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*[\[(]", line)
        if match is None:
            continue
        comment = []
        cursor = index - 1
        while cursor >= 0 and lines[cursor].strip().startswith("//"):
            comment.append(lines[cursor].strip()[2:].strip())
            cursor -= 1
        comment.reverse()
        found.append((match.group(1), index + 1, comment))
    return found


def is_go_exempt(name: str) -> bool:
    """Tell whether a Go function is exempt from contracts and coverage.

    Args:
        name: Function name.

    Returns:
        True for main, init and Test/Benchmark/Fuzz/Example prefixed names.

    Raises:
        None.
    """
    return name in ("main", "init") or name.startswith(GO_EXEMPT)


def check_go_source(text: str, path: str) -> list:
    """Check Go source for missing function contracts.

    Args:
        text: Go source code.
        path: Path used in the reported violations.

    Returns:
        List of Violation, empty when every non-exempt function has a comment
        with ``Args:``, ``Returns:`` and ``Errors:`` lines.

    Raises:
        None.
    """
    violations = []
    for name, line, comment in go_functions(text):
        if is_go_exempt(name):
            continue
        if not comment:
            violations.append(Violation(path, line, f"{name}: missing contract comment"))
            continue
        for section in ("Args", "Returns", "Errors"):
            if not any(item.startswith(f"{section}:") for item in comment):
                violations.append(Violation(path, line, f"{name}: contract lacks '{section}:'"))
    return violations


def function_refs(path: Path, text: str) -> list:
    """List the functions of a source file that must be covered by tests.

    Args:
        path: File path; its suffix selects the language.
        text: File content.

    Returns:
        FuncRef list; empty for unsupported suffixes.

    Raises:
        None.
    """
    if path.suffix == ".py":
        return python_function_refs(text)
    if path.suffix in CPP_SUFFIXES:
        return cpp_function_refs(text)
    if path.suffix == ".go":
        return [FuncRef(n, ln) for n, ln, _c in go_functions(text) if not is_go_exempt(n)]
    return []


def collect_covers(text: str) -> set:
    """Collect names listed in ``covers:`` labels.

    Args:
        text: Content of a test file.

    Returns:
        Set of bare names (the part after the last ``::`` or ``.``).

    Raises:
        None.
    """
    names = set()
    for match in COVERS_RE.finditer(text):
        for token in re.split(r"[,\s]+", match.group(1).strip()):
            token = token.rstrip("*/").strip()
            if token:
                names.add(re.split(r"::|\.", token)[-1])
    return names


def iter_files(root: Path, suffixes: Sequence[str]) -> list:
    """List source files below a directory, skipping build/cache folders.

    Args:
        root: Directory (or a single file) to search.
        suffixes: Accepted file suffixes, e.g. ``(".py",)``.

    Returns:
        Sorted list of matching Paths.

    Raises:
        None.
    """
    if root.is_file():
        return [root] if root.suffix in suffixes else []
    result = []
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts[:-1]
        if any(part in SKIP_DIRS or part.startswith(("build-", "cmake-build-")) for part in parts):
            continue
        if path.is_file() and path.suffix in suffixes:
            result.append(path)
    return result


def is_test_file(path: Path) -> bool:
    """Tell whether a file is a test file by its name.

    Args:
        path: File path.

    Returns:
        True for ``test_*.py``, ``*_test.py`` and ``*_test.go``.

    Raises:
        None.
    """
    return bool(re.match(r"(?:test_.*|.*_test)\.py$|.*_test\.go$", path.name))


def check_tree(root: Path, tests: Path | None, check_covers: bool) -> list:
    """Check all sources under a directory.

    Args:
        root: Directory with sources (or a single file).
        tests: Directory scanned for ``covers:`` labels; defaults to ``root``.
        check_covers: Also report functions without a ``covers:`` label.

    Returns:
        Violations sorted by path and line.

    Raises:
        None.
    """
    suffixes = (".py", ".go") + CPP_SUFFIXES
    tests_root = tests if tests is not None else root
    tests_resolved = tests_root.resolve()
    labels: set = set()
    if check_covers:
        for path in iter_files(tests_root, suffixes):
            labels |= collect_covers(path.read_text(encoding="utf-8", errors="replace"))
    violations = []
    for path in iter_files(root, suffixes):
        text = path.read_text(encoding="utf-8", errors="replace")
        shown = path.as_posix()
        if path.suffix == ".py":
            violations.extend(check_python_source(text, shown))
        elif path.suffix == ".go":
            violations.extend(check_go_source(text, shown))
        else:
            violations.extend(check_cpp_source(text, shown))
        in_tests = tests is not None and tests_resolved in path.resolve().parents
        if check_covers and not in_tests and not is_test_file(path):
            for ref in function_refs(path, text):
                if ref.name not in labels:
                    violations.append(Violation(shown, ref.line, f"{ref.name}: no 'covers:' label in tests"))
    return sorted(violations, key=lambda v: (v.path, v.line, v.message))


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """Command-line entry point.

    Args:
        argv: Arguments without the program name; defaults to ``sys.argv[1:]``.
        out: Text stream for the report; defaults to ``sys.stdout``.

    Returns:
        0 if no violations, 1 if there are violations, 2 if the directory does
        not exist.

    Raises:
        SystemExit: argparse exits on malformed command lines.
    """
    parser = argparse.ArgumentParser(prog="check_contracts.py", description="Lint function contracts.")
    parser.add_argument("root", help="directory (or file) with sources")
    parser.add_argument("--tests", help="directory with tests holding 'covers:' labels")
    parser.add_argument("--check-covers", action="store_true", help="require a covers label per function")
    args = parser.parse_args(argv)
    stream = out if out is not None else sys.stdout
    root = Path(args.root)
    if not root.exists():
        stream.write(f"error: {root} does not exist\n")
        return 2
    tests = Path(args.tests) if args.tests else None
    violations = check_tree(root, tests, args.check_covers)
    for violation in violations:
        stream.write(format_violation(violation) + "\n")
    return 1 if violations else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
