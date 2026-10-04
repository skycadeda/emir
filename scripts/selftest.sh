#!/bin/sh
# Offline proof that the install works. Prints SELFTEST PASSED.
# Resolves symlinks the same way bin/emir does.
self=$0
while [ -h "$self" ]; do
    dir=$(cd "$(dirname "$self")" && pwd -P)
    link=$(readlink "$self")
    case $link in
        /*) self=$link ;;
        *) self=$dir/$link ;;
    esac
done
exec "$(cd "$(dirname "$self")" && pwd -P)/../bin/emir" selftest "$@"
