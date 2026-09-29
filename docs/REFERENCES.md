# References

The single bibliography of this repository. Documents and code cite an entry as
`[ref:key]`; `tests/test_references.py` fails if a key does not resolve, if an
entry is never cited, if two entries share a title under different authors, if an
author list is a placeholder, or if a "Surname et al. (year)" in the text does not
match an entry's first author and year. The January 2026 bibliography failed two
of those checks (AUDIT.md item 9).

Every entry says how it was checked. **checked** means the details below were
read, on the date given, from the source named in "checked against", never filled
in from memory. **unchecked** means they come from a search engine's index of the
publisher's page, because the publisher's page could not be reached from the
environment the rest of the project was built in; those entries must be checked
against the publisher's page before anything is published, and they say so.

### ye2022
- authors: Junyao Ye; Jingyong Su; Yilong Cao
- title: A Stochastic Shortest Path Algorithm for Optimizing Spaced Repetition Scheduling
- year: 2022
- venue: ACM SIGKDD Conference on Knowledge Discovery and Data Mining (KDD 2022), pages 4381–4390
- doi: 10.1145/3534678.3539081
- status: checked
- checked against: the BibTeX record in the authors' repository, github.com/maimemo/SSP-MMC, README (authors, title, year, publisher ACM, DOI, pages), 2026-09-28. The conference name comes from the DOI record's search index; dl.acm.org itself was not reachable.

### reddy2016
- authors: Siddharth Reddy; Igor Labutov; Siddhartha Banerjee; Thorsten Joachims
- title: Unbounded Human Learning: Optimal Scheduling for Spaced Repetition
- year: 2016
- venue: ACM SIGKDD International Conference on Knowledge Discovery and Data Mining (KDD 2016), pages 1815–1824
- doi: 10.1145/2939672.2939850
- status: unchecked
- checked against: a search engine's index of dl.acm.org, dblp and arXiv (1602.07032), 2026-09-28. All three sites were blocked from the build environment.

### balkanski2023
- authors: Eric Balkanski; Noemie Perivier; Clifford Stein; Hao-Ting Wei
- title: Energy-Efficient Scheduling with Predictions
- year: 2023
- venue: Advances in Neural Information Processing Systems 36 (NeurIPS 2023)
- status: unchecked
- checked against: a search engine's index of proceedings.neurips.cc, 2026-09-28; the site was blocked from the build environment. The January 2026 proposal listed this paper with its authors as "[Authors]".

### fsrs-wiki
- authors: open-spaced-repetition contributors
- title: The Algorithm (awesome-fsrs wiki)
- year: 2026
- venue: github.com/open-spaced-repetition/awesome-fsrs/wiki/The-Algorithm
- status: checked
- checked against: the page source, raw.githubusercontent.com/wiki/open-spaced-repetition/awesome-fsrs/The-Algorithm.md, 2026-09-28: the FSRS-4.5 default parameters, the FSRS v4 formulas FSRS-4.5 keeps, the FSRS-4.5 forgetting curve, and the absence of a post-lapse clamp (AUDIT.md item 27). A wiki changes; the year is the year it was read.

### py-fsrs-2.5.1
- authors: Jarrett Ye
- title: fsrs 2.5.1 (py-fsrs), Free Spaced Repetition Scheduler
- year: 2024
- venue: Python Package Index, pypi.org/project/fsrs/2.5.1; source github.com/open-spaced-repetition/py-fsrs
- status: checked
- checked against: the PyPI metadata (name, version, author) and the installed source, which `benchmarks/fsrs_reference.py` runs, 2026-09-28. The release year is taken from PyPI's upload date in its JSON record.

### anki-manual
- authors: Ankitects and contributors
- title: The Anki Manual, Deck Options, FSRS
- year: 2026
- venue: docs.ankiweb.net/deck-options.html; source github.com/ankitects/anki-manual, src/deck-options.md
- status: checked
- checked against: the manual's source on GitHub, 2026-09-28: "The default is 90%, which offers a good balance of retention and workload. Above 90% the workload increases very quickly". docs.ankiweb.net itself was not reachable.

### nilsson1980
- authors: Nils J. Nilsson
- title: Principles of Artificial Intelligence
- year: 1980
- venue: Tioga Publishing Company, Palo Alto
- status: unchecked
- checked against: a search engine's index of the book's review in Artificial Intelligence (ScienceDirect) and of Stanford's digital repository, 2026-09-28; both were blocked from the build environment. Cited for the AO* algorithm.

### hansen2001
- authors: Eric A. Hansen; Shlomo Zilberstein
- title: LAO*: A heuristic search algorithm that finds solutions with loops
- year: 2001
- venue: Artificial Intelligence 129(1–2), pages 35–62
- status: unchecked
- checked against: a search engine's index of sciencedirect.com and of the authors' copy at rbr.cs.umass.edu, 2026-09-28; both were blocked from the build environment.

### dunlosky2013
- authors: John Dunlosky; Katherine A. Rawson; Elizabeth J. Marsh; Mitchell J. Nathan; Daniel T. Willingham
- title: Improving Students' Learning With Effective Learning Techniques: Promising Directions From Cognitive and Educational Psychology
- year: 2013
- venue: Psychological Science in the Public Interest 14(1), pages 4–58
- doi: 10.1177/1529100612453266
- status: unchecked
- checked against: a search engine's index of journals.sagepub.com, pubmed.ncbi.nlm.nih.gov and psychologicalscience.org, and ScienceDaily's report of the publisher's press release (2013-01-10), 2026-09-29: ten techniques reviewed; practice testing and distributed practice rated high utility; summarization, highlighting, the keyword mnemonic, imagery for text and rereading rated low. The publisher's page, PubMed and ERIC were blocked from the build environment, so the full text was not read.
