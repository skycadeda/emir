# Third-party notices

EMIR's own code is licensed under the MIT License (`LICENSE`; `NOTICE` holds the
copyright line, the disclaimer, the credit for the derived code below and the
trademark note). This file lists the
third-party code and data in the repository and the software that EMIR
**redistributes**, and under which licences.

## The wheel and the source tree

The `emir` wheel and this repository hold EMIR's own code and data, plus:

- **code derived from the Skycadeda Analog Framework (SAF) and from Skyforge**:
  portions of EMIR are derived from these two projects, both Copyright (c) 2026
  Skycadeda, and are licensed here by their copyright holder under the MIT
  License (`LICENSE`), as part of EMIR.
- **open-PDK data** in `pdks/` (the repository and the source distribution;
  not the wheel), every value cited to its file:

| process file | upstream | licence |
|---|---|---|
| `pdks/sky130.yaml` | SkyWater SKY130 PDK (<https://github.com/google/skywater-pdk>), as installed by open_pdks (<https://github.com/RTimothyEdwards/open_pdks>) | Apache-2.0 |
| `pdks/gf180mcu.yaml` | GlobalFoundries GF180MCU PDK (<https://github.com/google/gf180mcu-pdk>), as installed by open_pdks | Apache-2.0 |
| `pdks/ihp_sg13g2.yaml` | IHP SG13G2 Open PDK (<https://github.com/IHP-GmbH/IHP-Open-PDK>) | Apache-2.0 |

Their runtime dependencies (numpy, scipy, PyYAML, and the optional
matplotlib, klayout, mcp) are installed by pip from their own distributions
and are not redistributed by EMIR.

## The offline bundle

`emir-<version>-linux-x86_64.tar.gz` (built by `scripts/build_bundle.sh` in
CI and attached to a GitHub Release) redistributes a Python interpreter and
the packages below, so that it installs and runs with no network and no
system Python. Each package keeps its own licence files: the interpreter's in
`python/lib/python3.11/LICENSE.txt`, the libraries it links (OpenSSL, SQLite,
libffi, zlib, bzip2, xz, ncurses, libedit, Tcl/Tk and the others) in
`licenses/python-build-standalone/` (copied from the same build's full archive,
with its `PYTHON.json`), every Python package's in its `*.dist-info/` directory
under `python/lib/python3.11/site-packages/` (`licenses/` or `LICENSE*`), and the
wheels themselves are copied unchanged into `wheels/`. `licenses/GCC-RUNTIME.txt`
says where the source of the GCC runtime libraries in `numpy.libs/` and
`scipy.libs/` (libgfortran, libquadmath) is. The bundle also carries `pdks/`
(the open-PDK data above) and `examples/`. The versions are whatever the build resolved; the table
names the components, not the versions.

| component | licence | where it comes from |
|---|---|---|
| CPython 3.11 (python-build-standalone `install_only`) | Python Software Foundation License 2.0 | <https://github.com/astral-sh/python-build-standalone>, <https://www.python.org/> |
| libraries the standalone CPython build links (OpenSSL, SQLite, libffi, zlib, bzip2, xz, ncurses and others) | their own licences (Apache-2.0, public domain, MIT, zlib, BSD-style); python-build-standalone documents them per build | <https://github.com/astral-sh/python-build-standalone> |
| Tcl/Tk 8.6, with the itcl and thread extensions (shipped by the standalone build under `python/lib/`; the `tkinter` package itself is removed) | Tcl/Tk licence (BSD-style) | <https://www.tcl-lang.org/> |
| pip, setuptools (shipped with the standalone build) | MIT | <https://pypi.org/project/pip/>, <https://pypi.org/project/setuptools/> |
| numpy | BSD-3-Clause, with bundled OpenBLAS (BSD-3-Clause) and the GCC runtime libraries libgfortran and libquadmath (GPL-3.0 with the GCC Runtime Library Exception; LGPL-2.1) in `numpy.libs/` | <https://numpy.org/> |
| scipy | BSD-3-Clause, with the same bundled OpenBLAS and GCC runtime libraries in `scipy.libs/` | <https://scipy.org/> |
| PyYAML | MIT | <https://pyyaml.org/> |
| matplotlib | Matplotlib License (PSF-based), with bundled FreeType and Qhull under their own licences | <https://matplotlib.org/> |
| contourpy, cycler, kiwisolver (matplotlib dependencies) | BSD-3-Clause | <https://pypi.org/project/contourpy/>, <https://pypi.org/project/cycler/>, <https://pypi.org/project/kiwisolver/> |
| fonttools (matplotlib dependency) | MIT | <https://pypi.org/project/fonttools/> |
| pillow (matplotlib dependency) | MIT-CMU, with bundled image and font libraries (libjpeg-turbo, libpng, libtiff, libwebp, libavif, FreeType, HarfBuzz, Little CMS, OpenJPEG, Brotli, xz, zstd, libxcb) in `pillow.libs/` under their own licences | <https://python-pillow.org/> |
| packaging (matplotlib dependency) | Apache-2.0 OR BSD-2-Clause | <https://pypi.org/project/packaging/> |
| pyparsing (matplotlib dependency) | MIT | <https://pypi.org/project/pyparsing/> |
| python-dateutil (matplotlib dependency) | Apache-2.0 AND BSD-3-Clause | <https://pypi.org/project/python-dateutil/> |
| six (python-dateutil dependency) | MIT | <https://pypi.org/project/six/> |

When the dependency set in `scripts/build_bundle.sh` (`DEPS=`) changes, this
table changes with it; `tests/test_packaging.py` checks that every package the
build installs is named here.
