- `clm build --only-sections` is now repeatable: passing the flag multiple
  times accumulates all selector tokens instead of silently keeping only the
  last occurrence (which made a "build sections X, Y, Z" verification pass on
  a build that had only built Z). The single-flag comma-separated form keeps
  working, and an empty value in any occurrence is still rejected (#616).
