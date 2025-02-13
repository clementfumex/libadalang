#! /usr/bin/env sh

set -v
set -e

export PATH=$INSTALL_DIR/bin:$PATH
export GPR_PROJECT_PATH=$ADALIB_DIR/share/gpr

# Log the toolchain to use
which gcc
which gprbuild
gcc -v
gprbuild -v

# Log which Langkit commit is used
(cd langkit && git log HEAD^..HEAD | cat)

# Avoid pretty-printing, for now: gnatpp from GNAT Community 2018 is known not
# to work on Libadalang.
ada/manage.py generate -P

# Build the generated lexer alone first, as it takes a huge amount of memory.
# Only then build the rest in parallel.
gprbuild -p -Pbuild/lib/gnat/libadalang.gpr \
    -XBUILD_MODE=dev -XLIBRARY_TYPE=relocatable -XXMLADA_BUILD=relocatable \
    -XLIBADALANG_WARNINGS=true -c -u libadalang-lexer_state_machine.adb
# Restrict parallelism to avoid OOM issues
ada/manage.py build -j12

# Finally, run the testsuite
#
# TODO: adjust the Travis CI setup to provide a viable OCaml environment and
# enable the corresponding testcases.
ada/manage.py test --disable-ocaml -- -j16

# Install result and pack an archive
ada/manage.py install "$ADALIB_DIR"
mkdir -p upload
tar czf upload/libadalang-$TRAVIS_BRANCH-$TRAVIS_OS_NAME.tar.gz -C "$ADALIB_DIR" .
