- **Sync v3: a `translate_edit` item now accepts a `keep_twin` answer.** When a
  one-sided edit leaves the other language's cell a faithful rendering (e.g. you
  refined the German prose and the English is already correct), answer
  `{"key": …, "choice": "keep_twin"}` to record the new baseline and keep the
  twin verbatim — instead of re-supplying the unchanged twin body as a `body`
  answer. Documented in `clm info sync-agents`. (#566)
