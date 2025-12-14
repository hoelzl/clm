// -*- coding: utf-8 -*-
// %% [markdown] lang="de" tags=["slide"]
//
// # Test: Failing C++ Notebook
//
// This notebook intentionally fails to test error reporting for C++.

// %% [markdown] lang="en" tags=["slide"]
//
// # Test: Failing C++ Notebook
//
// This notebook intentionally fails to test error reporting for C++.

// %%
#include <iostream>

// %% [markdown] lang="de" tags=["subslide"]
//
// ## Successful Cell
//
// This cell should execute correctly.

// %% [markdown] lang="en" tags=["subslide"]
//
// ## Successful Cell
//
// This cell should execute correctly.

// %%
std::cout << "This cell runs successfully\n";

// %% [markdown] lang="de" tags=["subslide"]
//
// ## Failing Class Definition
//
// This class is missing a semicolon after the closing brace.

// %% [markdown] lang="en" tags=["subslide"]
//
// ## Failing Class Definition
//
// This class is missing a semicolon after the closing brace.

// %%
// This cell will fail - missing semicolon after class definition
class BrokenClass {
public:
    void DoNothing() {}
}

// %% [markdown] lang="de" tags=["subslide"]
//
// ## Never Reached
//
// This cell should never be executed because the previous cell fails.

// %% [markdown] lang="en" tags=["subslide"]
//
// ## Never Reached
//
// This cell should never be executed because the previous cell fails.

// %%
std::cout << "This should never be printed\n";
