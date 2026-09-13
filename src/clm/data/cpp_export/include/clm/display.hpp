#pragma once

// CLM support header for the C++ code export and the C++ notebooks
// (issues #333, #928).
//
// `SHOW(expr);` / `CLM_DISPLAY(expr);` prints the expression labeled with
// its own text — `i1 = 10` — so a program's output reads like the notebook
// transcript, and the notebook shows a value the same way (the xeus-cpp
// kernel prints nothing for a bare expression). Values without an
// operator<< print a placeholder; a void expression is just evaluated.
//
// No lambda is involved on purpose: xeus-cpp (clang-repl, 0.8) crashes
// when a lambda-based wrapper is instantiated a second time after new
// globals were defined in between. The expression is instead evaluated
// once as the left operand of an overloaded comma operator, which hands
// its value to `display`; a void expression falls back to the built-in
// comma operator and reaches the no-op overload.

#include <iostream>
#include <type_traits>
#include <utility>

namespace clm {

template <typename T>
concept Streamable = requires(std::ostream& os, const T& value) { os << value; };

// Right operand of the comma operator inside CLM_DISPLAY.
struct DisplayEnd {};

// The evaluated expression, carried by reference to `display`.
template <typename T>
struct Displayed {
    T&& value;
};

template <typename T>
Displayed<T> operator,(T&& value, DisplayEnd) {
    return Displayed<T>{std::forward<T>(value)};
}

template <typename T>
void display(const char* label, Displayed<T> shown) {
    std::cout << label << " = ";
    if constexpr (Streamable<std::remove_cvref_t<T>>) {
        const auto flags = std::cout.flags();
        std::cout << std::boolalpha << shown.value << "\n";
        std::cout.flags(flags);
    } else {
        std::cout << "<unprintable value>\n";
    }
}

// A void expression: evaluated by the macro, nothing to print.
inline void display(const char*, DisplayEnd) {}

}  // namespace clm

// MSVC reports C4834 ("discarding return value of function with
// [[nodiscard]]") for a nodiscard call as the left operand of the comma
// operator, although the overload passes the value on. Suppress it for
// the one statement the macro expands to.
#if defined(_MSC_VER) && !defined(__clang__)
#define CLM_DISPLAY_SUPPRESS_ __pragma(warning(suppress : 4834))
#else
#define CLM_DISPLAY_SUPPRESS_
#endif

// The expression is evaluated exactly once, in the caller's scope;
// #__VA_ARGS__ turns the expression tokens into the label.
#define CLM_DISPLAY(...) \
    CLM_DISPLAY_SUPPRESS_ ::clm::display(#__VA_ARGS__, (__VA_ARGS__, ::clm::DisplayEnd{}))

// The deck-facing spelling: notebooks write `SHOW(expr);` (the xeus-cpp
// kernel prints nothing for a bare expression, so this is how a deck
// shows a value in the notebook and in the exported program alike).
#define SHOW(...) CLM_DISPLAY(__VA_ARGS__)
