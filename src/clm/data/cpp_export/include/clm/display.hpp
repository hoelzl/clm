#pragma once

// CLM support header for the C++ code export and the C++ notebooks
// (issues #333, #928, #1037).
//
// `SHOW(expr);` / `CLM_DISPLAY(expr);` prints the expression labeled with
// its own text — `i1 = 10` — so a program's output reads like the notebook
// transcript, and the notebook shows a value the same way (the xeus-cpp
// kernel prints nothing for a bare expression). A void expression is just
// evaluated.
//
// How a value is printed (first match wins):
//   - strings and chars: as is; quoted when nested in a container or pair
//   - C arrays of non-char elements: their elements, not the decayed address
//   - anything with an operator<<: that operator (a user-defined one always
//     wins, so a class that is both a range and streamable prints as before)
//   - scoped enums: their underlying value
//   - std::optional: the value, or `nullopt`
//   - std::pair / std::tuple: `(a, b)` / `(a, b, c)`
//   - map-like ranges (with a `mapped_type`): `{k1: v1, k2: v2}`
//   - other ranges (vector, array, list, deque, set, span, ...): `{1, 2, 3}`,
//     capped at `max_elements` (`{..., ... (N more)}`)
//   - anything else: `<unprintable value>`
// Nested values are formatted recursively by the same rules.
//
// No lambda is involved on purpose: xeus-cpp (clang-repl, 0.8) crashes
// when a lambda-based wrapper is instantiated a second time after new
// globals were defined in between. The expression is instead evaluated
// once as the left operand of an overloaded comma operator, which hands
// its value to `display`; a void expression falls back to the built-in
// comma operator and reaches the no-op overload. The formatting helpers
// below are plain function templates for the same reason.

#include <cstddef>
#include <iostream>
#include <iterator>
#include <optional>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <utility>

namespace clm {

template <typename T>
concept Streamable = requires(std::ostream& os, const T& value) { os << value; };

namespace detail {

// Ranges longer than this print their first elements and a count.
inline constexpr std::size_t max_elements = 100;

template <typename T>
struct IsPair : std::false_type {};
template <typename A, typename B>
struct IsPair<std::pair<A, B>> : std::true_type {};

template <typename T>
struct IsTuple : std::false_type {};
template <typename... Ts>
struct IsTuple<std::tuple<Ts...>> : std::true_type {};

template <typename T>
struct IsOptional : std::false_type {};
template <typename T>
struct IsOptional<std::optional<T>> : std::true_type {};

template <typename T>
concept CharArray = std::is_array_v<T> &&
                    std::is_same_v<std::remove_cv_t<std::remove_extent_t<T>>, char>;

template <typename T>
concept StringLike = std::is_same_v<T, std::string> || std::is_same_v<T, std::string_view> ||
                     std::is_same_v<T, const char*> || std::is_same_v<T, char*> ||
                     CharArray<T>;

template <typename T>
concept Range = requires(const T& range) {
    std::begin(range);
    std::end(range);
};

template <typename T>
concept MapLike = Range<T> && requires { typename T::mapped_type; };

template <typename T>
concept ScopedEnum = std::is_enum_v<T> && !std::is_convertible_v<T, std::underlying_type_t<T>>;

template <typename T>
void write(std::ostream& os, const T& value, bool nested);

template <typename T>
void write_range(std::ostream& os, const T& range) {
    os << '{';
    std::size_t count = 0;
    for (const auto& element : range) {
        if (count < max_elements) {
            if (count > 0) {
                os << ", ";
            }
            if constexpr (MapLike<T>) {
                write(os, element.first, true);
                os << ": ";
                write(os, element.second, true);
            } else {
                write(os, element, true);
            }
        }
        ++count;
    }
    if (count > max_elements) {
        os << ", ... (" << (count - max_elements) << " more)";
    }
    os << '}';
}

template <typename T, std::size_t... I>
void write_tuple(std::ostream& os, const T& tuple, std::index_sequence<I...>) {
    os << '(';
    ((os << (I == 0 ? "" : ", "), write(os, std::get<I>(tuple), true)), ...);
    os << ')';
}

template <typename T>
void write(std::ostream& os, const T& value, bool nested) {
    using U = std::remove_cvref_t<T>;
    if constexpr (StringLike<U>) {
        if (nested) {
            os << '"' << value << '"';
        } else {
            os << value;
        }
    } else if constexpr (std::is_same_v<U, char>) {
        if (nested) {
            os << '\'' << value << '\'';
        } else {
            os << value;
        }
    } else if constexpr (std::is_array_v<U>) {
        write_range(os, value);
    } else if constexpr (Streamable<U>) {
        os << value;
    } else if constexpr (ScopedEnum<U>) {
        os << +static_cast<std::underlying_type_t<U>>(value);
    } else if constexpr (IsOptional<U>::value) {
        if (value) {
            write(os, *value, nested);
        } else {
            os << "nullopt";
        }
    } else if constexpr (IsPair<U>::value) {
        os << '(';
        write(os, value.first, true);
        os << ", ";
        write(os, value.second, true);
        os << ')';
    } else if constexpr (IsTuple<U>::value) {
        write_tuple(os, value, std::make_index_sequence<std::tuple_size_v<U>>{});
    } else if constexpr (Range<U>) {
        write_range(os, value);
    } else {
        os << "<unprintable value>";
    }
}

}  // namespace detail

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
    const auto flags = std::cout.flags();
    std::cout << label << " = " << std::boolalpha;
    detail::write(std::cout, shown.value, false);
    std::cout << "\n";
    std::cout.flags(flags);
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
