#!/bin/bash -p
# Only config.xml.in's literal merged release invokes this entry point.
set +x
set -euo pipefail
unset BASH_ENV ENV PERL5OPT PERL5LIB CDPATH GLOBIGNORE
export PATH=/usr/bin:/bin:/usr/sbin:/sbin LC_ALL=C
umask 077
HERE=$(cd "$(dirname "$0")" && pwd -P)
exec /usr/bin/perl "$HERE/bounded-run.pl" --stage "$@"
