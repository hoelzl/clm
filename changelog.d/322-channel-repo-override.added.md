Release channels with non-derived repo names are now fully declarative
(#322): a `<channel repo="...">` attribute overrides the derived repo name
verbatim (remote path and URL template still apply), so lang-scoped
channels whose name already carries the language no longer derive a
duplicated `-{lang}` segment. `clm release provision` additionally prefers
the channel working tree's actual `origin` over the derived URL when one
is configured — matching the `clm git` philosophy that push/commit operate
on whatever origin the repo actually has — so group shares target the real
project without manual GitLab API calls.
