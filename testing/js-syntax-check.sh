#! /bin/bash

# Without these, the script's exit status is that of its last command (popd), so
# a syntax error found by node would not make it fail.
set -e -o pipefail

# Setup:
#
# npm install esprima-next
#
# (esprima is out of date and hasn't been updated in a while)

if [[ $# -gt 0 ]] ; then
    echo $# args presented
    node -r fs  -e "require('esprima-next').parseModule(fs.readFileSync('$1').toString(), {tolerant: true})"
else
    pushd "$(dirname "$(dirname "$(realpath "$0")")")"
    find . \
         -path ./timer/electron -prune -or \
         -name node_modules -prune -or \
         -name '.?*' -prune -or \
         -path ./templates -prune -or \
         -name \*.js -print0 | \
       xargs --null -t -n 1 -I % \
             node -r fs  --abort-on-uncaught-exception \
                  -e "require('esprima-next').parseScript(fs.readFileSync('%').toString())"
    popd
fi
