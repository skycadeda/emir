"""EMIR -- EM / IR-drop screening of extracted analog layout."""
__version__ = "1.1.0.dev0"

#: said wherever results are presented: the console verdict, the HTML pages,
#: the run summary JSON (`disclaimer`) and `emir report`
SCREENING_NOTE = "Screening, not sign-off."

#: the maintainer's disclaimer (2026-10-02), said where the project is
#: introduced and where results are presented: README, docs, AGENTS.md, the
#: HTML footers, the run summary (`research_disclaimer`), `emir report`
RESEARCH_DISCLAIMER = ("EMIR is a research project, provided as is, with no warranty or "
                       "guarantee of accuracy. It is your responsibility to test and verify "
                       "its results for your process, tools and design before relying on "
                       "them.")

#: the same, as one short console line under the verdict
RESEARCH_LINE = ("Research project, provided as is, no guarantee of accuracy: test and verify "
                 "its results before relying on them.")

#: rcc's contract (the maintainer's decision of 2026-10-11), one sentence, the
#: same wherever it is stated: the docs, AGENTS.md, the skill, --period's help,
#: the report's window line (a test holds them to it)
RCC_CONTRACT = ("rcc is exact for the simulated record over the whole periods you give "
                "(--period); whether the testbench has settled is yours to establish, as for "
                "any .meas.")
