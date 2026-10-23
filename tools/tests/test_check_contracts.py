import io
from pathlib import Path

import pytest

import check_contracts as cc

PY_GOOD = '''
def add(a: int, b: int) -> int:
    """Add.

    Args:
        a: left.
        b: right.

    Returns:
        Sum.

    Raises:
        None.
    """
    return a + b
'''

CPP_GOOD = """
/**
 * @brief Adds numbers.
 * @param a left operand.
 * @param b right operand.
 * @return the sum.
 * @pre none.
 * @post none.
 * @throws does not throw.
 */
int add(int a, int b);
"""

GO_GOOD = """package x

// Add sums two numbers.
// Args: a, b ints.
// Returns: the sum.
// Errors: none.
func Add(a, b int) int { return a + b }
"""


def messages(violations):
    """Extract the message part of violations.

    Args:
        violations: Iterable of check_contracts.Violation.

    Returns:
        List of message strings.

    Raises:
        None.
    """
    return [v.message for v in violations]


# covers: format_violation
def test_format_violation():
    assert cc.format_violation(cc.Violation("a/b.py", 7, "oops")) == "a/b.py:7: oops"


# covers: has_section
def test_has_section_detects_indented_header():
    assert cc.has_section("x\n    Args:\n      a", "Args")
    assert not cc.has_section("Returns something", "Returns")
    assert not cc.has_section("see Args: inline", "Args")


# covers: python_functions
def test_python_functions_marks_nested_and_methods():
    import ast

    tree = ast.parse("class A:\n    def m(self):\n        def inner(): pass\ndef f(): pass\n")
    found = {node.name: nested for node, nested in cc.python_functions(tree)}
    assert found == {"m": False, "inner": True, "f": False}


# covers: check_python_source
def test_python_good_source_is_clean():
    assert cc.check_python_source(PY_GOOD, "x.py") == []


# covers: check_python_source
def test_python_missing_docstring():
    result = cc.check_python_source("def f():\n    pass\n", "x.py")
    assert messages(result) == ["f: missing contract docstring"]
    assert result[0].line == 1


# covers: check_python_source
def test_python_missing_sections():
    src = 'def f(a) -> int:\n    """Doc."""\n    return a\n'
    assert messages(cc.check_python_source(src, "x.py")) == [
        "f: docstring lacks 'Args:'",
        "f: docstring lacks 'Returns:'",
        "f: docstring lacks 'Raises:'",
    ]


# covers: check_python_source
def test_python_none_return_and_self_need_no_args_or_returns():
    src = 'class A:\n    def m(self) -> None:\n        """Doc.\n\n        Raises:\n            None.\n        """\n'
    assert cc.check_python_source(src, "x.py") == []


# covers: check_python_source
def test_python_test_functions_skipped_but_nested_and_helpers_checked():
    src = "def test_x():\n    def helper():\n        pass\n\ndef make():\n    pass\n"
    assert messages(cc.check_python_source(src, "x.py")) == [
        "helper: missing contract docstring",
        "make: missing contract docstring",
    ]


# covers: check_python_source
def test_python_varargs_count_as_params_and_syntax_error_reported():
    src = 'def f(*args) -> None:\n    """Doc.\n\n    Raises:\n        None.\n    """\n'
    assert messages(cc.check_python_source(src, "x.py")) == ["f: docstring lacks 'Args:'"]
    assert messages(cc.check_python_source("def (:\n", "x.py"))[0].startswith("syntax error")


# covers: python_function_refs
def test_python_function_refs_excludes_nested_and_tests():
    src = "def a():\n    def inner(): pass\nclass C:\n    def m(self): pass\ndef test_a(): pass\n"
    assert [r.name for r in cc.python_function_refs(src)] == ["a", "m"]
    assert cc.python_function_refs("def (:") == []


# covers: mask_cpp
def test_mask_cpp_blanks_comments_strings_and_preprocessor_keeping_layout():
    src = '#define X(a) a\nint f(); // c(\nconst char* s = "a(b);"; /* x\n y */ char c = \'{\';\n'
    masked = cc.mask_cpp(src)
    assert len(masked) == len(src) and masked.count("\n") == src.count("\n")
    assert "define" not in masked and "c(" not in masked and "a(b)" not in masked and "{" not in masked
    assert "int f();" in masked


# covers: mask_cpp
def test_mask_cpp_raw_string_and_digit_separator():
    src = 'auto s = R"x(f(); )" )x"; int n = 1\'000;\n'
    masked = cc.mask_cpp(src)
    assert "f()" not in masked and "1'000" in masked


# covers: find_doc_comment_before
def test_find_doc_comment_before():
    text = "/** doc */\nint f();\n/* plain */\nint g();\nint h();"
    assert cc.find_doc_comment_before(text, text.index("int f")) == "/** doc */"
    assert cc.find_doc_comment_before(text, text.index("int g")) is None
    assert cc.find_doc_comment_before(text, text.index("int h")) is None
    assert cc.find_doc_comment_before("int f();", 0) is None


# covers: split_params
def test_split_params_handles_templates_and_void():
    assert cc.split_params("int a, std::map<int, int> m, void (*cb)(int, int)") == [
        "int a",
        "std::map<int, int> m",
        "void (*cb)(int, int)",
    ]
    assert cc.split_params("") == []
    assert cc.split_params("void") == []


# covers: param_name
def test_param_name_cases():
    assert cc.param_name("const std::string& s") == "s"
    assert cc.param_name("int n = 5") == "n"
    assert cc.param_name("std::vector<int> v") == "v"
    assert cc.param_name("int arr[4]") == "arr"
    assert cc.param_name("void (*cb)(int)") == "cb"


# covers: param_name
def test_param_name_unnamed_parameters():
    for unnamed in ("int", "const Foo&", "unsigned int", "std::string", "const int", "struct Foo", "..."):
        assert cc.param_name(unnamed) is None, unnamed


# covers: parse_signature
def test_parse_signature_function_and_nonfunctions():
    func = cc.parse_signature("static int Foo::bar(int a, int b) const", 10)
    assert func.name == "bar" and func.qualifier == "Foo::" and func.params == ["int a", "int b"]
    assert func.start == 10 + 0
    for text in ("int x = f(3)", "std::function<void(int)> cb", "if (x)", "return g(1)", "TEST(A, B)",
                 "struct S", "using T = int", "int a(1), b"):
        assert cc.parse_signature(text, 0) is None, text


# covers: parse_signature
def test_parse_signature_default_delete_and_operators():
    assert cc.parse_signature("Foo(const Foo&) = delete", 0).skipped
    assert cc.parse_signature("Foo() = default", 0).skipped
    assert not cc.parse_signature("Foo(int a)", 0).skipped
    assert cc.parse_signature("bool operator()(int x) const", 0).name == "operator()"
    assert cc.parse_signature("bool operator<(const A& o) const", 0).name == "operator<"
    assert cc.parse_signature("public:\n  int f()", 0).name == "f"


# covers: parse_cpp_functions
def test_parse_cpp_functions_enters_scopes_and_skips_bodies():
    src = (
        "namespace n {\nstruct S {\n  int a();\n  int b() { return helper(1); }\n  int field{3};\n};\n"
        "enum class E { A, B };\nint free_fn(int x) { if (x) { return f(x); } return 0; }\n}\n"
    )
    assert [f.name for f in cc.parse_cpp_functions(src)] == ["a", "b", "free_fn"]


# covers: parse_cpp_functions
def test_parse_cpp_functions_templates_ctors_and_macros():
    src = (
        "template <typename T>\nT id(T v) { return v; }\n"
        "TEST(Suite, Case) { EXPECT_EQ(1, 1); }\n"
        "class C {\n public:\n  explicit C(int a) : a_(a) {}\n  ~C();\n};\n"
    )
    assert [f.name for f in cc.parse_cpp_functions(src)] == ["id", "C", "~C"]


# covers: cpp_returns_value
def test_cpp_returns_value_cases():
    def returns(text):
        """Parse a signature and answer cpp_returns_value for it.

        Args:
            text: Signature text.

        Returns:
            Result of cpp_returns_value.

        Raises:
            None.
        """
        return cc.cpp_returns_value(cc.parse_signature(text, 0))

    assert returns("int f()")
    assert returns("void* f()")
    assert returns("auto f() -> int")
    assert returns("static const std::string& f()")
    assert returns("operator bool() const")
    assert not returns("void f()")
    assert not returns("auto f() -> void")
    assert not returns("explicit C(int a)")
    assert not returns("~C()")
    assert not returns("C::C(int a)")
    assert not returns("template <typename T> void f(T v)")


# covers: check_cpp_source
def test_cpp_good_source_is_clean():
    assert cc.check_cpp_source(CPP_GOOD, "a.hpp") == []


# covers: check_cpp_source
def test_cpp_missing_param_return_throws_and_brief():
    src = "/** @param a x */\nint f(int a, int b);\n"
    assert messages(cc.check_cpp_source(src, "a.hpp")) == [
        "f: contract lacks @brief",
        "f: contract lacks @param b",
        "f: contract lacks @return",
        "f: contract lacks @throws",
    ]


# covers: check_cpp_source
def test_cpp_no_contract_at_all_reports_line():
    result = cc.check_cpp_source("\n\nint f();\n", "a.hpp")
    assert messages(result) == ["f: missing Doxygen contract comment"]
    assert result[0].line == 3


# covers: check_cpp_source
def test_cpp_void_needs_no_return_and_plain_comment_is_not_contract():
    ok = "/** @brief x\n * @throws none */\nvoid f();\n"
    assert cc.check_cpp_source(ok, "a.hpp") == []
    plain = "/* @brief x */\nvoid f();\n"
    assert messages(cc.check_cpp_source(plain, "a.hpp")) == ["f: missing Doxygen contract comment"]


# covers: check_cpp_source
def test_cpp_nolint_default_delete_and_test_macro_are_exempt():
    src = (
        "int f();  // NOLINT(contract)\n"
        "struct S {\n  S() = default;\n  S(const S&) = delete;\n};\n"
        "TEST(A, B) { int x = 1; }\n"
    )
    assert cc.check_cpp_source(src, "a.cpp") == []


# covers: check_cpp_source
def test_cpp_definition_and_member_function_checked():
    src = "struct S {\n  int g(int a) { return a; }\n};\nint S::h() { return 1; }\n"
    assert messages(cc.check_cpp_source(src, "a.cpp")) == [
        "g: missing Doxygen contract comment",
        "S::h: missing Doxygen contract comment",
    ]


# covers: cpp_function_refs
def test_cpp_function_refs_skip_deleted():
    refs = cc.cpp_function_refs("struct S {\n  S(const S&) = delete;\n  int f();\n};\n")
    assert [(r.name, r.line) for r in refs] == [("f", 3)]


# covers: go_functions
def test_go_functions_collect_comments_and_methods():
    found = cc.go_functions("// A does a.\n// Args: x\nfunc (r *T) A() {}\n\nfunc B() {}\nvar f = func() {}\n")
    assert [(n, ln, c) for n, ln, c in found] == [("A", 3, ["A does a.", "Args: x"]), ("B", 5, [])]


# covers: is_go_exempt
def test_is_go_exempt():
    assert cc.is_go_exempt("main") and cc.is_go_exempt("init")
    assert cc.is_go_exempt("TestX") and cc.is_go_exempt("BenchmarkY")
    assert not cc.is_go_exempt("Add") and not cc.is_go_exempt("testing")


# covers: check_go_source
def test_go_good_source_is_clean():
    assert cc.check_go_source(GO_GOOD, "a.go") == []


# covers: check_go_source
def test_go_missing_comment_and_sections():
    assert messages(cc.check_go_source("func Add() {}\n", "a.go")) == ["Add: missing contract comment"]
    partial = "// Add adds.\n// Args: none\nfunc Add() {}\n"
    assert messages(cc.check_go_source(partial, "a.go")) == [
        "Add: contract lacks 'Returns:'",
        "Add: contract lacks 'Errors:'",
    ]


# covers: check_go_source
def test_go_exempt_functions_are_ignored():
    assert cc.check_go_source("func main() {}\nfunc TestX(t *testing.T) {}\nfunc init() {}\n", "a.go") == []


# covers: function_refs
def test_function_refs_dispatch_by_suffix():
    assert [r.name for r in cc.function_refs(Path("a.py"), "def f(): pass")] == ["f"]
    assert [r.name for r in cc.function_refs(Path("a.hpp"), "int f();")] == ["f"]
    assert [r.name for r in cc.function_refs(Path("a.go"), "func F() {}\nfunc main() {}")] == ["F"]
    assert cc.function_refs(Path("a.txt"), "def f(): pass") == []


# covers: collect_covers
def test_collect_covers_parses_all_comment_styles():
    text = "# covers: alpha, Cls.beta\n// covers: ns::gamma delta\n/* covers: eps */\nno label"
    assert cc.collect_covers(text) == {"alpha", "beta", "gamma", "delta", "eps"}
    assert cc.collect_covers("") == set()


# covers: iter_files
def test_iter_files_filters_suffix_and_skips_build_dirs(tmp_path):
    for rel in ("a.py", "sub/b.py", "build/c.py", "build-x/d.py", "__pycache__/e.py", "f.txt"):
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")
    names = [p.relative_to(tmp_path).as_posix() for p in cc.iter_files(tmp_path, (".py",))]
    assert names == ["a.py", "sub/b.py"]
    assert cc.iter_files(tmp_path / "a.py", (".py",)) == [tmp_path / "a.py"]
    assert cc.iter_files(tmp_path / "f.txt", (".py",)) == []


# covers: is_test_file
def test_is_test_file():
    assert cc.is_test_file(Path("test_x.py")) and cc.is_test_file(Path("x_test.py"))
    assert cc.is_test_file(Path("x_test.go"))
    assert not cc.is_test_file(Path("x.py")) and not cc.is_test_file(Path("contest.py"))


# covers: check_tree
def test_check_tree_reports_all_languages_sorted(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n")
    (tmp_path / "b.hpp").write_text("int g();\n")
    (tmp_path / "c.go").write_text("func H() {}\n")
    result = cc.check_tree(tmp_path, None, False)
    assert [Path(v.path).name for v in result] == ["a.py", "b.hpp", "c.go"]


# covers: check_tree
def test_check_tree_covers_complete_and_incomplete(tmp_path):
    src = tmp_path / "src"
    tests = tmp_path / "tests"
    src.mkdir()
    tests.mkdir()
    (src / "m.py").write_text(PY_GOOD + PY_GOOD.replace("add", "sub"))
    (tests / "test_m.py").write_text("# covers: add\ndef test_add():\n    pass\n")
    result = cc.check_tree(src, tests, True)
    assert messages(result) == ["sub: no 'covers:' label in tests"]
    (tests / "test_m2.py").write_text("# covers: sub\n")
    assert cc.check_tree(src, tests, True) == []


# covers: check_tree
def test_check_tree_ignores_test_files_for_coverage_but_checks_their_contracts(tmp_path):
    (tmp_path / "test_x.py").write_text("def helper():\n    pass\ndef test_a():\n    pass\n")
    assert messages(cc.check_tree(tmp_path, None, True)) == ["helper: missing contract docstring"]
    own = tmp_path / "t"
    own.mkdir()
    (own / "util.py").write_text(PY_GOOD)
    assert cc.check_tree(own, own, True) == []


# covers: main
def test_main_exit_codes_and_output(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n    pass\n")
    out = io.StringIO()
    assert cc.main([str(tmp_path)], out) == 1
    assert "a.py:1: f: missing contract docstring" in out.getvalue()
    (tmp_path / "a.py").write_text(PY_GOOD)
    assert cc.main([str(tmp_path)], io.StringIO()) == 0


# covers: main
def test_main_check_covers_flag_and_missing_dir(tmp_path):
    (tmp_path / "a.py").write_text(PY_GOOD)
    tests = tmp_path / "tests"
    tests.mkdir()
    out = io.StringIO()
    code = cc.main([str(tmp_path), "--tests", str(tests), "--check-covers"], out)
    assert code == 1 and "add: no 'covers:' label" in out.getvalue()
    out = io.StringIO()
    assert cc.main([str(tmp_path / "nope")], out) == 2
    with pytest.raises(SystemExit):
        cc.main([], io.StringIO())


# covers: _strip_template_prefix
def test_strip_template_prefix_handles_nesting_and_absence():
    assert cc._strip_template_prefix("template <typename T, class U = std::vector<int>> void f()") == "void f()"
    assert cc._strip_template_prefix("  int f()") == "int f()"
    assert cc._strip_template_prefix("template <typename T") == "template <typename T"


# covers: _match_brace
def test_match_brace_nested_and_unbalanced():
    code = "{ a { b } c } d"
    assert cc._match_brace(code, 0) == code.index("} d")
    assert cc._match_brace("{ never closed", 0) == len("{ never closed") - 1
