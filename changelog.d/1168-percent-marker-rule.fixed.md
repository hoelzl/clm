- **Slide-text tools now agree with the build on cell markers.** A commented magic
  such as `# %%time` or `# %%cython -a` no longer starts a new cell in the slide-text
  parser, which the build never did. `clm validate` also reports `# %%[markdown]`, `#%%`,
  and an indented `# %%` as malformed cell markers (#1168).
