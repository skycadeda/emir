#!/bin/sh
# Make the console-script launchers in a bin/ directory relocatable.
#
#   sh scripts/relocate_launchers.sh <bin-dir> <build-path>
#
# pip writes each launcher (emir, f2py, pip ...) with the interpreter's
# absolute path, which is a path on the BUILD machine. It has two forms:
#
#   short path:  #!<build-path>/python/bin/python3            (one line)
#   long path:   #!/bin/sh                                     (three lines:
#                '''exec' <build-path>/python/bin/python3 "$0" "$@"
#                ' '''
#
# pip switches to the second form when the path is longer than a kernel
# accepts in a shebang (about 127 characters). Either form naming
# <build-path> is replaced by the same three-line trampoline, which runs the
# python3 next to the launcher wherever the bundle is untarred -- the real
# launcher's directory, found through any chain of symlinks (a link in
# ~/bin to python/bin/emir once ran ~/bin/python3, which is not there).
# Python reads the trampoline's second and third lines as one string
# literal, so the rest of the launcher is unchanged. Anything else
# (symlinks, binaries, scripts that do not name <build-path> in their
# header) is left alone.
#
# Called by build_bundle.sh (step 6b); tests/test_packaging.py runs it on
# both forms. POSIX sh plus GNU `chmod --reference`.
set -eu

if [ $# -ne 2 ]; then
  echo "usage: relocate_launchers.sh <bin-dir> <build-path>" >&2
  exit 2
fi
bindir=$1
build=$2

for f in "$bindir"/*; do
  if [ -L "$f" ] || [ ! -f "$f" ]; then continue; fi
  case $(head -c 2 "$f") in
    '#!') ;;
    *) continue;;
  esac
  first=$(head -n 1 "$f")
  header=0                       # how many lines of pip's header to replace
  case $first in
    *"$build"*) header=1;;
    '#!/bin/sh')
      second=$(sed -n 2p "$f")
      third=$(sed -n 3p "$f")
      case $second in
        "'''exec' "*"$build"*)
          if [ "$third" = "' '''" ]; then header=3; fi;;
      esac;;
  esac
  if [ "$header" = 0 ]; then continue; fi
  tmp=$f.reloc
  {
    printf '%s\n' '#!/bin/sh'
    # (one sh line: follow the links from $0, then exec the python3 beside
    # the real file; no three quotes in a row, which would end Python's string)
    printf '%s\n' "'''exec' \"\$(p=\$0; while [ -h \"\$p\" ]; do l=\$(readlink \"\$p\"); case \$l in /*) p=\$l;; *) p=\$(dirname -- \"\$p\")/\$l;; esac; done; dirname -- \"\$p\")/python3\" \"\$0\" \"\$@\""
    printf '%s\n' "' '''"
    tail -n +$((header + 1)) "$f"
  } > "$tmp"
  chmod --reference="$f" "$tmp"
  mv "$tmp" "$f"
done
