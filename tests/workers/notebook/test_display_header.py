"""Behavior of the vendored ``clm/display.hpp`` (``SHOW``) — issue #1037.

Compiles and runs a small program against the header and checks what each
``SHOW`` prints. Runs only when a C++20 compiler is on PATH.
"""

from __future__ import annotations

import importlib.resources
import shutil
import subprocess
from pathlib import Path

import pytest

_CXX = shutil.which("g++") or shutil.which("clang++")
_SUPPORT_INCLUDE_DIR = (
    Path(str(importlib.resources.files("clm"))) / "data" / "cpp_export" / "include"
)

_PROGRAM = r"""
#include <array>
#include <cstddef>
#include <list>
#include <map>
#include <optional>
#include <set>
#include <span>
#include <string>
#include <string_view>
#include <tuple>
#include <utility>
#include <vector>

#include <clm/display.hpp>

enum class Color { Red, Green };
enum Plain { A, B };
struct NoPrint {};
struct Point { int x{}, y{}; };
std::ostream& operator<<(std::ostream& os, const Point& p) {
    return os << "Point(" << p.x << ", " << p.y << ")";
}
// Both a range and streamable: its own operator<< must win.
struct MyVec {
    std::vector<int> data{1, 2};
    auto begin() const { return data.begin(); }
    auto end() const { return data.end(); }
};
std::ostream& operator<<(std::ostream& os, const MyVec&) { return os << "MyVec!"; }
std::vector<std::size_t> indices() { return {1, 3}; }
void nothing() {}

int main() {
    int i{10};
    SHOW(i);
    SHOW(true);
    SHOW(std::string{"hi"});
    SHOW('c');
    std::vector<int> v{1, 2, 3};
    SHOW(v);
    SHOW(indices());
    SHOW(std::vector<int>{});
    SHOW(std::vector<std::string>{"ab", "cd"});
    SHOW(std::vector<char>{'a', 'b'});
    SHOW(std::vector<bool>{true, false});
    SHOW(std::vector<std::vector<int>>{{1, 2}, {3}});
    SHOW(std::array<double, 2>{1.5, 2.5});
    SHOW(std::list<int>{4, 5});
    SHOW(std::set<int>{3, 1, 2});
    SHOW(std::map<std::string, int>{{"a", 1}, {"b", 2}});
    SHOW(std::vector<std::pair<int, int>>{{1, 2}});
    SHOW(std::pair<int, std::string>{1, "x"});
    SHOW(std::make_tuple(1, 'c', 2.5));
    SHOW(std::optional<int>{42});
    SHOW(std::optional<int>{});
    SHOW(Color::Green);
    SHOW(Plain::B);
    int arr[3]{7, 8, 9};
    SHOW(arr);
    SHOW(std::span<const int>{v});
    SHOW(std::string_view{"sv"});
    SHOW(MyVec{});
    SHOW(Point{1, 2});
    SHOW(std::vector<Point>{{1, 2}});
    SHOW(NoPrint{});
    std::vector<int> big(103, 0);
    SHOW(big.size());
    SHOW(nothing());
    std::cout << false << "\n";
}
"""

_EXPECTED = [
    "i = 10",
    "true = true",
    'std::string{"hi"} = hi',
    "'c' = c",
    "v = {1, 2, 3}",
    "indices() = {1, 3}",
    "std::vector<int>{} = {}",
    'std::vector<std::string>{"ab", "cd"} = {"ab", "cd"}',
    "std::vector<char>{'a', 'b'} = {'a', 'b'}",
    "std::vector<bool>{true, false} = {true, false}",
    "std::vector<std::vector<int>>{{1, 2}, {3}} = {{1, 2}, {3}}",
    "std::array<double, 2>{1.5, 2.5} = {1.5, 2.5}",
    "std::list<int>{4, 5} = {4, 5}",
    "std::set<int>{3, 1, 2} = {1, 2, 3}",
    'std::map<std::string, int>{{"a", 1}, {"b", 2}} = {"a": 1, "b": 2}',
    "std::vector<std::pair<int, int>>{{1, 2}} = {(1, 2)}",
    'std::pair<int, std::string>{1, "x"} = (1, "x")',
    "std::make_tuple(1, 'c', 2.5) = (1, 'c', 2.5)",
    "std::optional<int>{42} = 42",
    "std::optional<int>{} = nullopt",
    "Color::Green = 1",
    "Plain::B = 1",
    "arr = {7, 8, 9}",
    "std::span<const int>{v} = {1, 2, 3}",
    'std::string_view{"sv"} = sv',
    "MyVec{} = MyVec!",
    "Point{1, 2} = Point(1, 2)",
    "std::vector<Point>{{1, 2}} = {Point(1, 2)}",
    "NoPrint{} = <unprintable value>",
    "big.size() = 103",
    # SHOW of a void call prints nothing; the stream flags are restored
    # afterwards, so a plain `false` prints as 0 again.
    "0",
]


def _run(program: str, tmp_path: Path) -> list[str]:
    source = tmp_path / "show.cpp"
    source.write_text(program, encoding="utf-8")
    exe = tmp_path / "show.exe"
    build = subprocess.run(
        [
            _CXX,
            "-std=c++20",
            "-Wall",
            "-Wextra",
            f"-I{_SUPPORT_INCLUDE_DIR}",
            "-o",
            str(exe),
            str(source),
        ],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    return run.stdout.splitlines()


@pytest.mark.skipif(_CXX is None, reason="no C++ compiler on PATH")
class TestDisplayHeader:
    def test_show_formats_common_value_types(self, tmp_path):
        assert _run(_PROGRAM, tmp_path) == _EXPECTED

    def test_long_ranges_are_capped(self, tmp_path):
        program = (
            "#include <vector>\n#include <clm/display.hpp>\n"
            "int main() { std::vector<int> big(103, 0); SHOW(big); }\n"
        )
        (line,) = _run(program, tmp_path)
        assert line.startswith("big = {0, 0, ")
        assert line.endswith(", ... (3 more)}")
        assert line.count("0") == 100
