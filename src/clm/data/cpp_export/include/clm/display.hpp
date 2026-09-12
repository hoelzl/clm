#pragma once

// CLM support header for the C++ code export (issues #333, #928).
//
// In the notebooks, a bare expression on its own line is displayed by the
// kernel. The export wraps such expressions in CLM_DISPLAY(expr), which
// prints them labeled with the expression text — `i1 = 10` — so the
// program's output reads like the notebook transcript. Values without an
// operator<< print a placeholder; void expressions are just evaluated.

#include <iostream>
#include <type_traits>
#include <utility>

namespace clm {

template <typename T>
concept Streamable = requires(std::ostream& os, const T& value) { os << value; };

template <typename ExprThunk>
void display(const char* label, ExprThunk&& expr_thunk) {
    if constexpr (std::is_void_v<std::invoke_result_t<ExprThunk>>) {
        std::forward<ExprThunk>(expr_thunk)();
    } else {
        decltype(auto) value = std::forward<ExprThunk>(expr_thunk)();
        std::cout << label << " = ";
        if constexpr (Streamable<std::remove_cvref_t<decltype(value)>>) {
            const auto flags = std::cout.flags();
            std::cout << std::boolalpha << value << "\n";
            std::cout.flags(flags);
        } else {
            std::cout << "<unprintable value>\n";
        }
    }
}

}  // namespace clm

// The lambda evaluates the expression exactly once, in the caller's scope;
// #__VA_ARGS__ turns the expression tokens into the label.
#define CLM_DISPLAY(...) \
    ::clm::display(#__VA_ARGS__, [&]() -> decltype(auto) { return (__VA_ARGS__); })
