# j2 from 'macros.j2' import header
# {{ header("Voiceover Test", "Voiceover Test") }}

# %% [markdown] lang="de" tags=["slide"]
# ## Thema Eins
#
# Inhalt auf Deutsch.

# %% [markdown] lang="de" tags=["voiceover"]
# Hier ist der Voiceover-Text für Thema Eins.

# %% [markdown] lang="en" tags=["slide"]
# ## Topic One
#
# Content in English.

# %% [markdown] lang="en" tags=["voiceover"]
# Here is the voiceover text for topic one.

# %% tags=["keep"]
x = 1

# %% [markdown] lang="de" tags=["subslide"]
# ## Thema Zwei
#
# Mehr Inhalt.

# %% [markdown] lang="en" tags=["subslide"]
# ## Topic Two
#
# More content.

# %% tags=["keep"]
print(x)
