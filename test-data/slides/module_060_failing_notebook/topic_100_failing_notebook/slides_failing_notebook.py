# j2 from 'macros.j2' import header
# {{ header("Fehlerhafte Notebook", "Failing Notebook") }}

# %%
print("This cell runs successfully")

# %% [markdown]
#
# This notebook intentionally fails to test error reporting.

# %%
# This cell will fail with a NameError
undefined_variable_that_does_not_exist
