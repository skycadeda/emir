## What and why

<!-- What changes, why, and the issue it closes (Fixes #NN). For a change to a
number EMIR reports: how the new number is checked (a closed form, an example
with a hand calculation, another tool). -->

## Gates (CONTRIBUTING.md, "The gates")

- [ ] `pre-commit run --all-files` passes: ruff, the leak guard, and the three
      generator checks (`gen_errors_md.py`, `gen_schemas.py`, `gen_cadence_skill.py --check`)
- [ ] `python -m pytest` passes (every marker; the Cadence ones need a licence and are skipped in CI)
- [ ] `emir selftest` and `emir validate` pass
- [ ] every bug fix and every feature comes with a test
- [ ] a new failure or warning has its own `EMIR-` code (`emir/errors.py`), and
      `agent/errors.md` and `agent/schemas/` are regenerated
- [ ] `CHANGELOG.md` has an entry under `[Unreleased]`
- [ ] documentation, playbooks and `emir next` rules follow the code (docs follow the code, never the reverse)
- [ ] SKILL changes (`.il`, `emir/skillgui.py`, `emir/skillapp.py`): tried in a live
      Virtuoso session (`agent/cadence/LIVE_TEST.md`), or marked UNVERIFIED in
      `agent/cadence/README.md`

## Confidentiality and numbers

- [ ] No foundry or licensed-PDK data in the diff or the commit messages: no DSPF,
      process limits, ICT, layer, model or display-packet names, rule values; no site
      paths, host names or licence servers (the leak guard catches many, not all)
- [ ] Every number added to code comments, docs or examples has a source: a script in
      this repository that reproduces it, a cited public document, or a measurement
      quoted as such. Results are called screening, never sign-off.

## Sign-off

- [ ] Every commit is signed off (`git commit -s`): I certify the
      [Developer Certificate of Origin 1.1](https://developercertificate.org/)
