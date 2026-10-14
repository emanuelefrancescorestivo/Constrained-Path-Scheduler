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

The entries below are the evidence behind the product's engagement design
(docs/MARKET.md §3, DECISIONS.md D9 and D12). All were found by web search on
2026-10-08 from an environment that could search but not open pages, so all are
**unchecked**. Where a source gave surnames only, the entry gives surnames only.

### lally2010
- authors: P. Lally; C. van Jaarsveld; H. Potts; J. Wardle
- title: How are habits formed: Modelling habit formation in the real world
- year: 2010
- venue: European Journal of Social Psychology 40, pages 998–1009
- doi: 10.1002/ejsp.674
- status: unchecked
- checked against: a search engine's index of UCL's news release (August 2009), the British Psychological Society's Research Digest and secondary summaries, 2026-10-08: 96 participants, a daily behaviour for 84 days, time to automaticity 18 to 254 days (median 66 among good model fits), missing one opportunity did not materially affect habit formation. The publisher's page was not reachable.

### silverman2023
- authors: Jackie Silverman; Alixandra Barasch
- title: On or Off Track: How (Broken) Streaks Affect Consumer Decisions
- year: 2023
- venue: Journal of Consumer Research
- doi: 10.1093/jcr/ucac029
- status: unchecked
- checked against: a search engine's index of the University of Delaware repository (udspace.udel.edu), the University of Colorado's faculty pages and news release, 2026-10-08: seven studies; intact streaks shown in logs increase later engagement relative to broken ones; the effect is larger when people blame themselves and smaller when the streak can be repaired. The full text was not read.

### gollwitzer2006
- authors: Gollwitzer; Sheeran
- title: Implementation intentions and goal achievement: A meta-analysis of effects and processes
- year: 2006
- venue: Advances in Experimental Social Psychology 38, pages 69–119
- doi: 10.1016/S0065-2601(06)38002-1
- status: unchecked
- checked against: a search engine's index of the University of Konstanz repository (kops.uni-konstanz.de) and secondary summaries, 2026-10-08: 94 tests, d = 0.65 for goal attainment. The chapter itself was not read.

### hamari2014
- authors: Hamari; Koivisto; Sarsa
- title: Does Gamification Work? A Literature Review of Empirical Studies on Gamification
- year: 2014
- venue: Proceedings of the 47th Hawaii International Conference on System Sciences (HICSS)
- status: unchecked
- checked against: a search engine's index of Aalto University's research portal and the authors' research group's page (gamification-research.org), 2026-10-08: mostly positive effects, dependent on context and users; novelty effects among the recurring caveats. The paper's count of 24 studies comes from a secondary summary.

### koivisto2019
- authors: Koivisto; Hamari
- title: The rise of motivational information systems: A review of gamification research
- year: 2019
- venue: journal article, as recorded by the University of Turku repository (utupub.fi); the journal was not confirmed from the publisher
- status: unchecked
- checked against: a search engine's index of utupub.fi and research.utu.fi, 2026-10-08: 819 studies reviewed; results lean positive with a remarkable amount of mixed results.

### castro2025
- authors: Castro; Valença
- title: Teaching or Manipulating? On the Adoption of Bright and Deceptive Patterns by Duolingo
- year: 2025
- venue: Brazilian Symposium on Human Factors in Computing Systems (IHC), SBC Open Library (sol.sbc.org.br)
- status: unchecked
- checked against: a search engine's index of sol.sbc.org.br and deceptive.design, 2026-10-08: a qualitative review of Duolingo's interface finding manipulative patterns (excessive notifications, emotionally charged visuals) next to ethical ones. The year is the search summary's.

### hepi2025
- authors: Josh Freeman
- title: Student Generative AI Survey 2025
- year: 2025
- venue: Higher Education Policy Institute (HEPI) and Kortext, hepi.ac.uk
- status: unchecked
- checked against: a search engine's index of hepi.ac.uk, 2026-10-08: 1,041 UK undergraduates surveyed by Savanta; 92 % use AI in some form, 88 % for assessments; deterred by fear of being accused of cheating (53 %) and false results (51 %).

The entries below are the evidence behind the analytics (docs/ANALYTICS.md,
DECISIONS.md D22 to D24). All were found by web search on 2026-10-08 and read as
abstracts or secondary summaries, from an environment that could search but not
open pages, so all are **unchecked**. Where the sources gave an incomplete author
list, the entry says so instead of completing it from memory.

### harkin2016
- authors: Harkin; Webb; Chang; further authors, not listed by the sources read
- title: Does monitoring goal progress promote goal attainment? A meta-analysis of the experimental evidence
- year: 2016
- venue: Psychological Bulletin 142(2), pages 198–229
- doi: 10.1037/bul0000025
- status: unchecked
- checked against: a search engine's index of the White Rose eprints record (eprints.whiterose.ac.uk/91437) and secondary summaries, 2026-10-08: 138 randomised studies, N = 19,951; monitoring interventions increased monitoring (d+ = 1.98) and goal attainment (d+ = 0.40, 95 % CI 0.32 to 0.48); larger effects when progress was reported or physically recorded; mostly health behaviours. The full text was not read.

### kivetz2006
- authors: Kivetz; Urminsky; Zheng
- title: The Goal-Gradient Hypothesis Resurrected: Purchase Acceleration, Illusionary Goal Progress, and Customer Retention
- year: 2006
- venue: Journal of Marketing Research 43(1), pages 39–58
- doi: 10.1509/jmkr.43.1.39
- status: unchecked
- checked against: a search engine's index of Columbia Business School's research pages and the authors' working paper (business.columbia.edu), 2026-10-08: café customers bought more often as a free coffee came closer; a 12-stamp card with 2 bonus stamps was completed faster than a 10-stamp card; raters visited more and quit less near the reward. The published article was not read.

### etkin2016
- authors: Jordan Etkin
- title: The Hidden Cost of Personal Quantification
- year: 2016
- venue: Journal of Consumer Research 42(6), pages 967–984
- status: unchecked
- checked against: a search engine's index of Duke Fuqua's summary and the manuscript on marketing.wharton.upenn.edu, 2026-10-08: six experiments (walking, reading, colouring); measurement increased output and decreased enjoyment, continued engagement and subjective well-being. The published article was not read.

### deci1999
- authors: E. L. Deci; R. Koestner; R. M. Ryan
- title: A Meta-Analytic Review of Experiments Examining the Effects of Extrinsic Rewards on Intrinsic Motivation
- year: 1999
- venue: Psychological Bulletin 125(6), pages 627–668
- status: unchecked
- checked against: a search engine's index of author-hosted copies (depts.washington.edu and others), 2026-10-08: 128 experiments; engagement-, completion- and performance-contingent rewards lowered free-choice motivation (d = −0.40, −0.36, −0.28); positive feedback enhanced it, as the abstract's excerpt began to say before it was cut; contested by Cameron and Pierce. The full text was not read.

### foster2001
- authors: Foster; seven further authors, not listed by the sources read
- title: A New Approach to Monitoring Exercise Training
- year: 2001
- venue: Journal of Strength and Conditioning Research 15(1), pages 109–115
- status: unchecked
- checked against: a search engine's index of the abstract (scinapse.io, sponet.de) and a later review of the method (PMC5673663), 2026-10-08: session RPE (a 0 to 10 rating times the session's minutes) tracked a heart-rate method in cycling and basketball. The paper was not read.

### strava-ff
- authors: Strava, Inc.
- title: Fitness & Freshness
- year: 2026
- venue: Strava Support, support.strava.com (a help article; the year is the year it was consulted)
- status: unchecked
- checked against: a search engine's index of support.strava.com and road.cc, 2026-10-08: a subscriber chart of fitness, fatigue and form from Training Load or Relative Effort (heart rate or perceived exertion), an impulse-response model; "the overall numbers aren't as important as general trends". The help article itself was not opened.

### koriat2005
- authors: Asher Koriat; Robert A. Bjork
- title: Illusions of competence in monitoring one's knowledge during study
- year: 2005
- venue: Journal of Experimental Psychology: Learning, Memory, and Cognition 31(2), pages 187–194
- status: unchecked
- checked against: a search engine's index of the University of Haifa's research portal (cris.haifa.ac.il) and an author-hosted PDF, 2026-10-08: judgments of learning made while the answer is present overestimate later recall. The PDF was not opened.

### adesope2017
- authors: Adesope; Trevisan; Sundararajan
- title: Rethinking the Use of Tests: A Meta-Analysis of Practice Testing
- year: 2017
- venue: Review of Educational Research 87(3), pages 659–701
- doi: 10.3102/0034654316689306
- status: unchecked
- checked against: a search engine's index of journals.sagepub.com, WSU's repository and summaries (Learning Scientists, Klingenstein Center), 2026-10-08: practice tests beat rereading and every other comparison condition; summaries disagree on the number of studies (118 or 188 articles, 272 effect sizes). The article was not read.

### cepeda2006
- authors: Cepeda; Pashler; Vul; Wixted; Rohrer
- title: Distributed practice in verbal recall tasks: A review and quantitative synthesis
- year: 2006
- venue: Psychological Bulletin 132(3), pages 354–380
- doi: 10.1037/0033-2909.132.3.354
- status: unchecked
- checked against: a search engine's index of PubMed (16719566) and the authors' copies, 2026-10-08: 839 assessments from 317 experiments in 184 articles; the inter-study interval giving the best retention grows with the retention interval. The article was not read.

### roig2013
- authors: Marc Roig; Sasja Nordbrandt; Svend Sparre Geertsen; Jens Bo Nielsen
- title: The effects of cardiovascular exercise on human memory: A review with meta-analysis
- year: 2013
- venue: Neuroscience & Biobehavioral Reviews 37(8), pages 1645–1666
- status: unchecked
- checked against: a search engine's index of the University of Copenhagen's research portal and later papers citing it, 2026-10-08: acute exercise, SMD 0.26 on short-term memory and 0.52 on long-term memory; long-term training, SMD 0.15 on short-term memory; results vary with the memory tested, intensity and timing. The article was not read.

### prokopidis2023
- authors: Prokopidis; further authors, not listed by the sources read
- title: Effects of creatine supplementation on memory in healthy individuals: a systematic review and meta-analysis of randomized controlled trials
- year: 2023
- venue: Nutrition Reviews 81(4) (online August 2022)
- status: unchecked
- checked against: a search engine's index of PMC9999677 and news summaries, 2026-10-08: 10 trials reviewed, 8 meta-analysed; SMD 0.29 (95 % CI 0.04 to 0.53, I² = 66 %); clearer in the two trials of older adults (66 to 76) than in younger people. The article was not read.

### the2021
- authors: Anna McKie
- title: StuDocu: academics angry as lecture notes shared without consent
- year: 2021
- venue: Times Higher Education, 29 September 2021
- status: unchecked
- checked against: a search engine's index of timeshighereducation.com, 2026-10-08: lecturers' handouts, slides and test questions found on the platform beside students' notes; 15 million users, 2,000 universities and over 4 million documents as the company reported; premium access bought or earned by uploading. The article itself was not opened.

The entries below are the evidence of the engine review (docs/ENGINE-REVIEW.md,
DECISIONS.md D44 onwards), looked up on 2026-10-13 from an environment that could
open most pages. **checked** says which page was read; **unchecked** says where a
detail came from when the publisher's page could not be opened.

### kukushkin2024
- authors: N. V. Kukushkin; R. E. Carney; T. Tabassum; T. J. Carew
- title: The massed-spaced learning effect in non-neural human cells
- year: 2024
- venue: Nature Communications 15
- doi: 10.1038/s41467-024-53922-x
- status: checked
- checked against: the IDEAS/RePEc record (title, authors, journal, volume, year, DOI, abstract), 2026-10-13: two immortalised non-neural cell lines with a CREB-dependent luciferase reporter; four spaced pulses gave stronger, more sustained expression and ERK and CREB activation than one massed pulse; blocking ERK or CREB removed the effect. nature.com redirected to a login and was not read.

### cepeda2008
- authors: Cepeda; Vul; Rohrer; Wixted; Pashler
- title: Spacing Effects in Learning: A Temporal Ridgeline of Optimal Retention
- year: 2008
- venue: Psychological Science 19(11), pages 1095–1102
- status: unchecked
- checked against: the abstract on the University of South Florida's Scholar Commons record (digitalcommons.usf.edu/psy_facpub/1766), 2026-10-13: more than 1,350 people, a review after gaps of up to 3.5 months, a final test up to a year later; the best gap was about 20 to 40 % of a one-week delay and 5 to 10 % of a one-year delay. Authors' surnames, volume and pages come from a search engine's index; PubMed and the PDF could not be read.

### karpicke2009
- authors: Jeffrey D. Karpicke; Andrew C. Butler; Henry L. Roediger III
- title: Metacognitive strategies in student learning: Do students practise retrieval when they study on their own?
- year: 2009
- venue: Memory 17(4), pages 471–479
- doi: 10.1080/09658210802647009
- status: checked
- checked against: the article's PDF hosted by Washington University in St. Louis (title page and abstract), 2026-10-13: 177 college students surveyed; a majority repeatedly reread notes or textbooks, relatively few practised retrieval; the authors propose illusions of competence.

### rasch2013
- authors: Björn Rasch; Jan Born
- title: About Sleep's Role in Memory
- year: 2013
- venue: Physiological Reviews 93(2), pages 681–766
- status: checked
- checked against: PMC3768102 (abstract and opening sections), 2026-10-13: sleep actively consolidates memory; reactivation during slow-wave sleep, coordinated by slow oscillations, spindles and ripples, moves memories to long-term cortical storage.

### mazza2016
- authors: Stéphanie Mazza; further authors, not listed by the sources read
- title: Relearn Faster and Retain Longer: Along With Practice, Sleep Makes Perfect
- year: 2016
- venue: Psychological Science
- doi: 10.1177/0956797616659930
- status: unchecked
- checked against: the Association for Psychological Science's press release (design and results), 2026-10-13: 40 adults, 16 French–Swahili pairs; relearning 12 hours later took about 3 trials after sleep against about 6 without; a week later about 15 pairs against 11. Title and DOI come from a search engine's index; the article was not read.

### vandongen2016
- authors: van Dongen; further authors, not listed by the sources read
- title: Physical Exercise Performed Four Hours after Learning Improves Memory Retention and Increases Hippocampal Pattern Similarity during Retrieval
- year: 2016
- venue: Current Biology
- status: unchecked
- checked against: Harvard Health Publishing's summary, 2026-10-13: 72 people learned 90 picture-location pairs; 35 minutes of interval training four hours later improved recall 48 hours later, immediately after did not. Title and authors come from a search engine's index; the article was not read.

### smarr2018
- authors: Benjamin L. Smarr; Aaron E. Schirmer
- title: 3.4 million real-world learning management system logins reveal the majority of students experience social jet lag correlated with decreased performance
- year: 2018
- venue: Scientific Reports
- status: checked
- checked against: PMC5876324, 2026-10-13: 14,894 students over four semesters at Northeastern Illinois University; about 60 % with social jet lag; misalignment correlated with lower grades; observational, one university.

### bettencourt2024
- authors: C. Bettencourt; L. Pires; F. Almeida; M. Vilar; H. Cruz; J. Leitão; A. A. Gomes
- title: Chronotype, Time of Day, and Children's Cognitive Performance in Remote Neuropsychological Assessment
- year: 2024
- venue: Behavioral Sciences 14(4), 310
- status: checked
- checked against: PMC11047315 (its review of the literature), 2026-10-13: synchrony and asynchrony effects are both reported, the evidence is mixed, most of it in adults; a meta-analysis of students found eveningness related to cognitive ability with r = 0.08.

### pan2023
- authors: S. C. Pan; S. K. Carpenter
- title: Prequestioning and pretesting effects: A review of empirical research, theoretical perspectives, and implications for educational practice
- year: 2023
- venue: Educational Psychology Review 35(4)
- doi: 10.1007/s10648-023-09814-5
- status: unchecked
- checked against: a reference list in a search engine's index, 2026-10-13; the publisher's page redirected to a login. Its findings are taken from the Psychonomic Society's summary [ref:sana2023].

### sana2023
- authors: Faria Sana
- title: Test first, learn later: The power of pretesting to enhance learning
- year: 2023
- venue: Psychonomic Society, Featured Content, 29 August 2023
- status: checked
- checked against: the post itself (featuredcontent.psychonomic.org), 2026-10-13: a narrative summary of pretesting studies; about 12 percentage points higher on a later exam; 13 points on end-of-week quizzes and 12 on midterms up to seven weeks later in Yan and Sana's study. No meta-analysis; the author's own studies are among those described.

### rohrer2020
- authors: D. Rohrer; R. F. Dedrick; M. K. Hartwig; C.-N. Cheung
- title: A Randomized Controlled Trial of Interleaved Mathematics Practice
- year: 2020
- venue: Journal of Educational Psychology 112(1), pages 40–52
- status: checked
- checked against: the What Works Clearinghouse's review (ies.ed.gov/ncee/wwc/Study/88770), 2026-10-13: 787 grade-7 students, cluster-randomised; interleaved 62.17 against blocked 37.60 on a test 43 days later; meets standards without reservations.

### srs-benchmark
- authors: open-spaced-repetition contributors
- title: SRS Benchmark
- year: 2026
- venue: GitHub, github.com/open-spaced-repetition/srs-benchmark
- status: checked
- checked against: the repository's README, 2026-10-13: 9,999 collections and about 350 million reviews without same-day reviews; log loss, RMSE (bins) and AUC per model; FSRS-6 0.3460, FSRS-4.5 0.3625, FSRS-7 default 0.3620 and fitted 0.3401, LSTM 0.3332, RWKV-Instant 0.2773 (log loss).

### py-fsrs-2026
- authors: open-spaced-repetition contributors
- title: py-fsrs (README, October 2026)
- year: 2026
- venue: GitHub, github.com/open-spaced-repetition/py-fsrs
- status: checked
- checked against: the repository's README, 2026-10-13: MIT licence, 21 weights by default, an `optimizer` extra (`pip install "fsrs[optimizer]"`) that fits parameters to a review log; no recommended minimum number of reviews is given.

### py-fsrs-6.3.2
- authors: Jarrett Ye, Joshua Hamilton
- title: fsrs 6.3.2 (py-fsrs), Free Spaced Repetition Scheduler
- year: 2026
- venue: Python Package Index, pypi.org/project/fsrs/6.3.2; source github.com/open-spaced-repetition/py-fsrs
- status: checked
- checked against: the PyPI JSON record (name, version, authors, MIT licence, uploaded 2026-08-09) and the installed source, which `benchmarks/fsrs6_reference.py` runs, 2026-10-14: the 21 default weights, the FSRS-6 forgetting curve with its decay weight, the same-day update, the post-lapse cap.

### typesafe-jev
- authors: TypeSafe AI
- title: Jev documentation and home page
- year: 2026
- venue: docs.typesafe.ai and typesafe.ai
- status: checked
- checked against: docs.typesafe.ai/introduction and typesafe.ai, 2026-10-13: a "System One" model returning typed answers (choice, score, a yes/no probability) with confidence; "193.6x Faster, 444.6x Cheaper" on TypeSafe's own System One workflows; early access; no latency, data-handling or deployment terms stated on those pages.

### firecrawl-jev
- authors: Firecrawl
- title: What Is Jev? Inside TypeSafe's Decision-Only AI Model and Its Developer Use Cases
- year: 2026
- venue: firecrawl.dev blog
- status: checked
- checked against: the post, 2026-10-13: TypeSafe's list of limits for Jev 1.13 (weak at maths and counting, dates read as text, multi-hop questions less accurate); hosted access only; sign-ups paused on 22 September 2026; independent tests reported by Every and others.

### or-tools
- authors: Google
- title: OR-Tools, with the CP-SAT solver
- year: 2026
- venue: github.com/google/or-tools and developers.google.com/optimization
- status: checked
- checked against: the repository's README (Apache License 2.0; CP-SAT included) and the CP-SAT page (Python, C++, Java, C#), 2026-10-13; the PyPI JSON record of ortools 9.15.6755 (Apache 2.0, uploaded 2026-01-14), the version `benchmarks/cpsat_trial.py` ran with, 2026-10-14.
