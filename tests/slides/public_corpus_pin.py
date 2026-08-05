"""The public-corpus pin (#682): which ClmTestCourse commit the gates assert on.

One constant, imported by the gate module and read by
``scripts/fetch_test_corpus.py``, so the fetch and the assertion can never
disagree. Bump it DELIBERATELY, together with the expected numbers in
``test_public_corpus.py`` — the whole point of the pin is that a gate failure
means *CLM changed*, never "the corpus moved underneath us".
"""

PUBLIC_CORPUS_REPO = "https://github.com/hoelzl/ClmTestCourse.git"
PUBLIC_CORPUS_PIN = "c536d5bb5ded8cb60a49180cbb64fa9f8e03e17b"  # 2026-08-05
