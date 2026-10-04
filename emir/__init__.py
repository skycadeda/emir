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
