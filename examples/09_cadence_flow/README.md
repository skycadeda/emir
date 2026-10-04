# 09 -- The Cadence flow, offline

What a Cadence site does with EMIR, run here without any Cadence tool: the
extraction **plan** for a block (PVS LVS, the MATCH gate, Quantus), the
Spectre save lines for its testbench, and the RCC testbench filled in. Nothing
licensed runs: `emir extract` without `--run` only prints what it would do.

```sh
sh examples/09_cadence_flow/run.sh            # writes examples/09_cadence_flow/out/
EMIR="python3 -m emir" sh examples/09_cadence_flow/run.sh /tmp/ex09   # a new or empty directory
```

`EMIR` is the emir command (default: `emir` on `$PATH`, else this clone's
`bin/emir`).

## What it does

1. **`emir extract` with nothing configured** (`out/plan_unconfigured.json`):
   `ready` is false and `missing` names the rule deck, the Quantus technology
   directory, the corner, the temperature (and the tools, when they are not on
   `$PATH`), each with where to set it. EMIR assumes no corner and no
   temperature.
2. **`emir extract --site out/site.yaml`** for `--kind r_only` and
   `--kind c_only_coupled` (`out/plan_*.json`): the two extractions of the
   big-block flow, from one LVS, into `railblock.r_only.dspf` and
   `railblock.c_only_coupled.dspf`. Each plan holds the PVS command line, the
   gate, the Quantus command and the full command file (52 command lines, every
   load-bearing setting in it). The decks in `out/pdk/` are empty stand-ins; on
   a real site the site file names the PDK's.
3. **`emir save-lines --mode rcc --prefix XDUT. --out out/tb/emir_save.scs`**
   on the bundled sample: the person's mode, rcc (VDD has 17 nodes, so `emir next`
   recommends it), saves every branch current: one line of 16 `XDUT.R...:1` signals.
4. **The RCC testbench template** (`agent/cadence/spectre/tb_rcc.scs.tmpl`)
   filled for the sample: `out/tb/tb_rcc.scs`. Its models line is a placeholder.
5. `summarize.py` writes `out/summary.json`, the machine-independent digest
   `tests/test_example_09_cadence_flow.py` compares with `expected/summary.json`.

## At a Cadence site

Point a site file at the real decks (`agent/cadence/site/emir_site.cadence.yaml.example`),
check the plan, and -- once the person agrees to run the licensed tools -- add
`--run`. Then simulate the testbench in Spectre with `-format psfascii` and run
`emir run ... --cc-sim <result> --mode rcc` (small block) or `--mode cc-grid`
(big block, with the R-only DSPF). `agent/cadence/README.md` has the whole flow.

Status: this example runs no Cadence tool. The extraction commands and the command
file are UNVERIFIED until checked on site: the local agent verifies them where PVS
and Quantus run (`emir extract --run`'s LVS MATCH gate, then `emir check` on the DSPF
it writes), and Spectre with `emir verify-sim --sim spectre --run`. Screening, not
sign-off.
