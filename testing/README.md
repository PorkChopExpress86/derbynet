# DerbyNet test suite

The tests are shell scripts that drive a running DerbyNet server over HTTP, plus
a few Puppeteer scripts in `puppeteer/`.  `exercise-one-database.sh` runs all of
them.  The `php-suite` GitHub Actions workflow runs the suite against the PHP
server image on every push and pull request.

## Run the suite on Linux

You need `docker` and `bash`.  From the root of a git checkout, run:

    testing/docker/run-suite.sh

The script does everything inside containers, and takes a while (a first run
also downloads and builds the images).  It

1. builds a harness image with the test tools,
2. builds the gitignored files the PHP pages need (`website/inc/banner.inc`,
   `website/timer.php`, and so on),
3. builds the server image from `installer/docker/Dockerfile` and starts it with
   a fresh data volume,
4. raises the PHP and nginx timeouts, which `test-extended-scheduling.sh` needs,
5. runs `test-face-detection.sh`, `js-syntax-check.sh`, and
   `exercise-one-database.sh http://localhost ez`, and
6. removes the server container and its volume.

A successful run prints the line

    <<<<<<<<<<<<<<<<<<<<<<<< Test run complete >>>>>>>>>>>>>>>>>>>>>>>>

as the last line of the suite's output.  Any failure stops the run with
`TEST FAILURE` and the last response received.  `testing/output.curl`,
`testing/debug.curl`, and `testing/suite.log` hold the details.

Name the steps to run some of them, e.g. `run-suite.sh harness assets server`
to leave a server running, and then `run-suite.sh suite`; `run-suite.sh clean`
removes the server.  Set `DERBYNET_TEST_PREFIX` to change the prefix
(`derbynet-test`) on the names of the images, container, and volume.

Run from a regular checkout, not a `git worktree`: the containers can't see the
repository's git directory, and the build needs it.

A server database can't be reused: setup (`ez-new=testdb`) fails with
`already_exists` the second time, so each run needs a fresh data volume.  The
script always makes one.

## Run individual scripts from the host

The scripts need `bash`, `curl`, `jq`, `xmllint` (Debian and Ubuntu package
`libxml2-utils`), and `md5sum`.  The Puppeteer tests and `js-syntax-check.sh`
also need `node`: run `npm ci` in `testing/puppeteer` and
`export NODE_PATH=$PWD/testing/puppeteer/node_modules`.

Start a server that listens on a port of localhost:

    DERBYNET_TEST_PORT=8080 testing/docker/run-suite.sh harness assets server timeout

Then give the script the server's URL, for example

    bash testing/exercise-one-database.sh http://localhost:8080 ez

Stop the server with `testing/docker/run-suite.sh clean`.

## macOS

`suite-local-mac.sh` runs the suite against a DerbyNet installed on the local
machine.
