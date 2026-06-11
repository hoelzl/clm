#pragma once

// CLM shim for the xeus-cpp <xcpp/xdisplay.hpp> header (issue #333).
//
// Course notebooks call xcpp::display(value) for rich output in Jupyter.
// This shim lets the exported CMake projects compile and run the same code
// outside the notebooks: like the kernel, it prefers an ADL-found
// mime_bundle_repr(value) overload (printing the "text/plain" entry), falls
// back to operator<<, and prints a placeholder otherwise.

#include <iostream>
#include <string>

#include <nlohmann/json.hpp>

// xeus provides this alias; course headers rely on it (e.g. for
// `nl::json mime_bundle_repr(...)` overloads).
namespace nl = nlohmann;

namespace xcpp {

template <typename T>
concept HasMimeBundleRepr = requires(const T& value) { mime_bundle_repr(value); };

template <typename T>
concept Streamable = requires(std::ostream& os, const T& value) { os << value; };

template <typename T>
void display(const T& value) {
    if constexpr (HasMimeBundleRepr<T>) {
        const nl::json bundle = mime_bundle_repr(value);
        const auto it = bundle.find("text/plain");
        if (it != bundle.end() && it->is_string()) {
            std::cout << it->get<std::string>() << "\n";
        } else {
            std::cout << bundle.dump() << "\n";
        }
    } else if constexpr (Streamable<T>) {
        const auto flags = std::cout.flags();
        std::cout << std::boolalpha << value << "\n";
        std::cout.flags(flags);
    } else {
        std::cout << "<unprintable value>\n";
    }
}

}  // namespace xcpp
