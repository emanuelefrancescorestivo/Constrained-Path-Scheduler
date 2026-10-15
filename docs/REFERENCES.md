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
- status: checked
- checked against: the abstract in Europe PMC's record of PubMed 26173288 (authors, title, journal, volume, pages, DOI), 2026-10-15: ten techniques reviewed; practice testing and distributed practice rated high utility; elaborative interrogation, self-explanation and interleaved practice moderate; summarization, highlighting, the keyword mnemonic, imagery for text and rereading low ("practice testing instead of rereading"). On 2026-09-29 only a search engine's index could be read; the monograph itself was not read.

The entries below are the evidence behind the product's engagement design
(docs/MARKET.md §3, DECISIONS.md D9 and D12). All were found by web search on
2026-10-08 from an environment that could search but not open pages, so all were
**unchecked**; those a tip cites (DECISIONS.md D62) were read at a research
database on 2026-10-15 and are marked checked. Where a source gave surnames only,
the entry gives surnames only.

### lally2010
- authors: Phillippa Lally; Cornelia H. M. van Jaarsveld; Henry W. W. Potts; Jane Wardle
- title: How are habits formed: Modelling habit formation in the real world
- year: 2010
- venue: European Journal of Social Psychology 40(6), pages 998–1009
- doi: 10.1002/ejsp.674
- status: checked
- checked against: the publisher's abstract as deposited with Crossref (the DOI's record), 2026-10-15: 96 volunteers chose an eating, drinking or activity behaviour to carry out daily for 12 weeks; 82 gave enough data; the time to reach 95 % of their asymptote of automaticity ranged from 18 to 254 days; missing one opportunity did not materially affect habit formation. Crossref dates the online publication 16 July 2009; the volume is 2010's. The median of 66 days comes from secondary summaries (2026-10-08), not from the abstract.

### silverman2023
- authors: Jackie Silverman; Alixandra Barasch
- title: On or Off Track: How (Broken) Streaks Affect Consumer Decisions
- year: 2023
- venue: Journal of Consumer Research
- doi: 10.1093/jcr/ucac029
- status: unchecked
- checked against: a search engine's index of the University of Delaware repository (udspace.udel.edu), the University of Colorado's faculty pages and news release, 2026-10-08: seven studies; intact streaks shown in logs increase later engagement relative to broken ones; the effect is larger when people blame themselves and smaller when the streak can be repaired. The full text was not read.

### gollwitzer2006
- authors: Peter M. Gollwitzer; Paschal Sheeran
- title: Implementation intentions and goal achievement: A meta-analysis of effects and processes
- year: 2006
- venue: Advances in Experimental Social Psychology 38, pages 69–119
- doi: 10.1016/S0065-2601(06)38002-1
- status: checked
- checked against: the abstract in Semantic Scholar's record of the DOI and Crossref's record (authors, title, volume, pages), 2026-10-15: 94 independent tests; implementation intentions ("If situation Y is encountered, then I will initiate goal-directed behavior X!") had a positive effect of medium-to-large magnitude (d = .65) on goal attainment. The chapter itself was not read.

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
open pages, so all were **unchecked**; those a tip cites (DECISIONS.md D62) were
read at a research database on 2026-10-15 and are marked checked. Where the
sources gave an incomplete author list, the entry says so instead of completing it
from memory.

### harkin2016
- authors: B. Harkin; T. L. Webb; B. P. Chang; A. Prestwich; M. Conner; I. Kellar; Y. Benn; P. Sheeran
- title: Does monitoring goal progress promote goal attainment? A meta-analysis of the experimental evidence
- year: 2016
- venue: Psychological Bulletin 142(2), pages 198–229
- doi: 10.1037/bul0000025
- status: checked
- checked against: the abstract in Europe PMC's record of PubMed 26479070, 2026-10-15: 138 studies (N = 19,951) randomly allocating an intervention to promote progress monitoring against a control; monitoring rose (d+ = 1.98) and goal attainment rose (d+ = 0.40, 95 % CI 0.32 to 0.48); larger effects when outcomes were reported or made public and when the information was physically recorded. That most studies were about health behaviours comes from secondary summaries (2026-10-08), not from the abstract.

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
- authors: Olusola O. Adesope; Dominic A. Trevisan; Narayankripa Sundararajan
- title: Rethinking the Use of Tests: A Meta-Analysis of Practice Testing
- year: 2017
- venue: Review of Educational Research 87(3), pages 659–701
- doi: 10.3102/0034654316689306
- status: checked
- checked against: the publisher's abstract as deposited with Crossref (api.crossref.org, the DOI's record), 2026-10-15: practice tests were more beneficial for learning than restudying and all other comparison conditions; mean effects were moderated by the tests' features, the participants and studies, the outcomes and the methods. The article was not read, so its count of studies is not given (summaries say 118 or 188 articles, 272 effect sizes).

### cepeda2006
- authors: N. J. Cepeda; H. Pashler; E. Vul; J. T. Wixted; D. Rohrer
- title: Distributed practice in verbal recall tasks: A review and quantitative synthesis
- year: 2006
- venue: Psychological Bulletin 132(3), pages 354–380
- doi: 10.1037/0033-2909.132.3.354
- status: checked
- checked against: the abstract in Europe PMC's record of PubMed 16719566, 2026-10-15: 839 assessments of distributed practice in 317 experiments located in 184 articles; the inter-study interval producing maximal retention increased as the retention interval increased. The article was not read.

### roig2013
- authors: Marc Roig; Sasja Nordbrandt; Svend Sparre Geertsen; Jens Bo Nielsen
- title: The effects of cardiovascular exercise on human memory: A review with meta-analysis
- year: 2013
- venue: Neuroscience & Biobehavioral Reviews 37(8), pages 1645–1666
- doi: 10.1016/j.neubiorev.2013.06.012
- status: checked
- checked against: the abstract in Europe PMC's record of PubMed 23806438, 2026-10-15: 29 and 21 studies of acute and long-term interventions; acute exercise SMD 0.26 on short-term memory and 0.52 (moderate to large) on long-term memory; long-term training SMD 0.15 on short-term memory and 0.07 (not significant) on long-term memory. The article was not read.

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
- authors: N. J. Cepeda; E. Vul; D. Rohrer; J. T. Wixted; H. Pashler
- title: Spacing Effects in Learning: A Temporal Ridgeline of Optimal Retention
- year: 2008
- venue: Psychological Science 19(11), pages 1095–1102
- doi: 10.1111/j.1467-9280.2008.02209.x
- status: checked
- checked against: the abstract in Europe PMC's record of PubMed 19076480 (authors, journal, volume, pages, DOI), 2026-10-15, as on the University of South Florida's Scholar Commons record (digitalcommons.usf.edu/psy_facpub/1766), 2026-10-13: more than 1,350 people, a review after gaps of up to 3.5 months, a final test up to a year later; the optimal gap was about 20 to 40 % of a 1-week test delay and 5 to 10 % of a 1-year delay.

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
- authors: Stéphanie Mazza; Emilie Gerbier; Marie-Paule Gustin; Zumrut Kasikci; Olivier Koenig; Thomas C. Toppino; Michel Magnin
- title: Relearn Faster and Retain Longer
- year: 2016
- venue: Psychological Science 27(10), pages 1321–1330
- doi: 10.1177/0956797616659930
- status: checked
- checked against: the abstract in Europe PMC's record of PubMed 27530500 and Crossref's record of the DOI (authors, title, journal, volume, pages), 2026-10-15: 40 participants practised foreign vocabulary to a perfect level in two sessions 12 hours apart, half with a night's sleep between them; sleeping between the sessions halved the practice needed and gave much better retention a week and six months later. The Association for Psychological Science's press release (2026-10-13) adds the subtitle "Along With Practice, Sleep Makes Perfect", which the records do not carry.

### vandongen2016
- authors: E. V. van Dongen; I. H. P. Kersten; I. C. Wagner; R. G. M. Morris; G. Fernández
- title: Physical Exercise Performed Four Hours after Learning Improves Memory Retention and Increases Hippocampal Pattern Similarity during Retrieval
- year: 2016
- venue: Current Biology 26(13), pages 1722–1727
- doi: 10.1016/j.cub.2016.04.071
- status: checked
- checked against: the abstract in Europe PMC's record of PubMed 27321998, 2026-10-15: three randomly assigned groups learned picture-location associations; exercise 4 hours later, but not immediately after, improved retention at a cued-recall test 48 hours later. The number of participants (72) and the exercise (35 minutes of interval training) come from Harvard Health Publishing's summary, 2026-10-13, not from the abstract.

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
- authors: Steven C. Pan; Shana K. Carpenter
- title: Prequestioning and pretesting effects: A review of empirical research, theoretical perspectives, and implications for educational practice
- year: 2023
- venue: Educational Psychology Review 35(4), article 97
- doi: 10.1007/s10648-023-09814-5
- status: checked
- checked against: the publisher's abstract as deposited with Crossref (the DOI's record), 2026-10-15: prequestioning or pretesting can benefit learning if there is an opportunity to study the correct answers afterwards; shown with texts, videos and lectures despite many wrong first answers; the enhancement may vary with the procedure and how learning is assessed; the mechanisms need further research. The publisher's page redirected to a login on 2026-10-13; the review itself was not read.

### yang2021
- authors: Chunliang Yang; Liang Luo; Miguel A. Vadillo; Rongjun Yu; David R. Shanks
- title: Testing (quizzing) boosts classroom learning: A systematic and meta-analytic review
- year: 2021
- venue: Psychological Bulletin 147(4), pages 399–435
- doi: 10.1037/bul0000309
- status: checked
- checked against: the abstract in Europe PMC's record of PubMed 33683913 (authors, journal, volume, pages, DOI), 2026-10-15: 48,478 students' data from 222 independent studies; testing raised achievement to a medium extent (g = 0.499), modulated by, among others, the learning strategy in the control condition, test format consistency, corrective feedback, the number of test repetitions and the design. The full text was not read.

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
- doi: 10.1037/edu0000367
- status: checked
- checked against: the What Works Clearinghouse's review (ies.ed.gov/ncee/wwc/Study/88770), 2026-10-13 and again 2026-10-15: 787 grade-7 students, cluster-randomised; interleaved 62.17 against 37.60 for the comparison group (labelled "business as usual") on a test the authors wrote, 43 days later; an improvement index of 31; meets standards without reservations. The DOI, journal, volume and pages from Crossref's record, 2026-10-15; Crossref carries no abstract.

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

The entries below are the evidence behind the tips (DECISIONS.md D62), looked up on
2026-10-15. Each was read at the record named in "checked against": Europe PMC's
copy of a PubMed record, the publisher's abstract as deposited with Crossref, or the
publisher's own page. A tip may cite only a checked entry (`tests/test_tips.py`).

### okano2019
- authors: Kana Okano; Jakub R. Kaczmarzyk; Neha Dave; John D. E. Gabrieli; Jeffrey C. Grossman
- title: Sleep quality, duration, and consistency are associated with better academic performance in college students
- year: 2019
- venue: npj Science of Learning 4, article 16
- doi: 10.1038/s41539-019-0055-z
- status: checked
- checked against: the abstract in Europe PMC's record of PubMed 31583118 (PMC6773696) and Crossref's record, 2026-10-15: wearable trackers given to 100 students of an introductory college chemistry class, 88 completing; better quality, longer and more consistent sleep correlated with better grades; no relation for the single night before a test, while the week and the month before did correlate; sleep measures accounted for nearly 25 % of the variance in academic performance. nature.com redirected to a login.

### albulescu2022
- authors: Patricia Albulescu; Irina Macsinga; Andrei Rusu; Coralia Sulea; Alexandra Bodnaru; Bogdan Tudor Tulbure
- title: "Give me a break!" A systematic review and meta-analysis on the efficacy of micro-breaks for increasing well-being and performance
- year: 2022
- venue: PLOS ONE 17(8), e0272460
- doi: 10.1371/journal.pone.0272460
- status: checked
- checked against: the abstract in Europe PMC's record of PubMed 36044424 (PMC9432722) and Crossref's record, 2026-10-15: 19 records, 22 independent samples (N = 2,335); micro-breaks (10 minutes or less) raised vigor (d = .36) and reduced fatigue (d = .35); no significant effect on overall performance (d = .16), significant only for less demanding tasks; longer breaks, larger boosts; recovering from highly depleting tasks may need more than 10 minutes.

### biwer2023
- authors: Felicitas Biwer; Wisnu Wiradhany; Mirjam G. A. oude Egbrink; Anique B. H. de Bruin
- title: Understanding effort regulation: Comparing 'Pomodoro' breaks and self-regulated breaks
- year: 2023
- venue: British Journal of Educational Psychology 93(S2), pages 353–367
- doi: 10.1111/bjep.12593
- status: checked
- checked against: the abstract in Europe PMC's record of PubMed 36859717 and Crossref's record, 2026-10-15: 87 bachelor's and master's students of a Dutch university, one day of self-study; self-regulated breaks (n = 35) against 6 minutes after every 24 ("Pomodoro", n = 25) or 3 after every 12 (n = 27); self-regulating gave longer sessions and breaks, with more fatigue and distractedness and less concentration and motivation; no difference in mental effort or task completion; systematic breaks "appeared to have efficiency benefits (i.e., similar task completion in shorter time)".

### brunmair2019
- authors: Matthias Brunmair; Tobias Richter
- title: Similarity matters: A meta-analysis of interleaved learning and its moderators
- year: 2019
- venue: Psychological Bulletin 145(11), pages 1029–1052
- doi: 10.1037/bul0000209
- status: checked
- checked against: the abstract in Europe PMC's record of PubMed 31556629 and Crossref's record, 2026-10-15: 59 studies, 238 effect sizes in 158 samples; a moderate interleaving effect overall (g = 0.42), paintings 0.67, mathematical tasks 0.34; expository texts and tastes ambiguous; blocking better for words (g = −0.39); stronger effects for material more similar between categories.

### crede2010
- authors: Marcus Credé; Sylvia G. Roch; Urszula M. Kieszczynka
- title: Class Attendance in College
- year: 2010
- venue: Review of Educational Research 80(2), pages 272–295
- doi: 10.3102/0034654310362998
- status: checked
- checked against: the publisher's abstract as deposited with Crossref (the DOI's record), 2026-10-15: attendance correlated with class grades (k = 69, N = 21,195, ρ = .44) and GPA (k = 33, N = 9,243, ρ = .41), better than any other known predictor, including admission test scores, high school GPA, study habits and study skills; mandatory attendance had a small positive effect (k = 3, d = .21). The title is Crossref's; the publisher's page was not opened.

### camfield2014
- authors: David A. Camfield; Con Stough; Jonathon Farrimond; Andrew B. Scholey
- title: Acute effects of tea constituents L-theanine, caffeine, and epigallocatechin gallate on cognitive function and mood: a systematic review and meta-analysis
- year: 2014
- venue: Nutrition Reviews 72(8), pages 507–522
- doi: 10.1111/nure.12120
- status: checked
- checked against: the abstract in Europe PMC's record of PubMed 24946991 and Crossref's record, 2026-10-15: 11 randomised placebo-controlled studies; moderate effects in favour of caffeine with L-theanine in the first 2 hours after a dose on alertness and attentional switching accuracy, and to a lesser extent some attention measures; trends toward a greater change in effect size with the caffeine dose than with the L-theanine dose. PubMed's own page asked for cookies.

### drake2013
- authors: Christopher Drake; Timothy Roehrs; John Shambroom; Thomas Roth
- title: Caffeine Effects on Sleep Taken 0, 3, or 6 Hours before Going to Bed
- year: 2013
- venue: Journal of Clinical Sleep Medicine 9(11), pages 1195–1200
- doi: 10.5664/jcsm.3170
- status: checked
- checked against: the article in PubMed Central (PMC3805807) and the abstract in Europe PMC's record of PubMed 24235903, 2026-10-15: 400 mg of caffeine at bedtime, 3 or 6 hours before it, against a placebo, at home; each disrupted sleep significantly; objectively measured total sleep time fell by 1.1 to 1.2 hours in each caffeine condition; the diary's 41 minutes at 6 hours only approached significance (p = 0.08); 12 subjects completed the protocol.

### efsa2015
- authors: EFSA Panel on Dietetic Products, Nutrition and Allergies (NDA)
- title: Scientific Opinion on the safety of caffeine
- year: 2015
- venue: EFSA Journal 13(5), 4102
- doi: 10.2903/j.efsa.2015.4102
- status: checked
- checked against: the abstract in Semantic Scholar's record of the DOI, Crossref's record, and EFSA's topic page (efsa.europa.eu/en/topics/topic/caffeine), 2026-10-15: single doses up to 200 mg and habitual intakes up to 400 mg a day give no safety concerns for healthy non-pregnant adults; up to 200 mg a day in pregnancy; a single 100 mg dose may affect sleep duration and patterns in some adults, particularly close to bedtime (the topic page). The publisher's page (onlinelibrary.wiley.com) answered 403. EFSA's 2011 opinion on L-theanine's health claims (EFSA Journal 9(6), 2238) could not be read and is not cited.
