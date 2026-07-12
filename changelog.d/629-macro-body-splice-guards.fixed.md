- Hardened the sync-apply j2 macro body writer (#629, follow-up to #609/#624):
  bare replacement text is now rejected when the macro line carries zero or
  multiple quoted arguments (previously only the first argument of a
  multi-argument macro would have been rewritten, silently keeping the rest),
  and bare text containing a backslash — which could escape out of the j2
  string literal — is rejected alongside `"`.
