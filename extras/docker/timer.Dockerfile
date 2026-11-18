# syntax=docker/dockerfile:1.7
#
# The timer image (#59): derby-timer.jar with a Java runtime, for a Raspberry
# Pi beside the track.  It runs the jar headless (-x) against the server that
# DERBYNET_SERVER names; the timer's serial port comes in with --device.  The
# build context is the repository root:
#
#   docker buildx build --platform linux/arm64 -f extras/docker/timer.Dockerfile -t derbynet_timer .
#
# docs/pi-setup.md says how to run it, and compose.timer.yaml next to this
# file runs it at boot.

ARG UBUNTU=ubuntu:26.04@sha256:065aa8626841f16057b78b027b464cd59c4fac36c204c72dcbf48d66f5fa8a96

# The jar, once, on the build machine's own platform: Java bytecode is the
# same on every platform, and jssc.jar carries its own linux_arm64 library.
FROM --platform=$BUILDPLATFORM ${UBUNTU} AS jar
RUN apt-get update \
 && apt-get install -y --no-install-recommends openjdk-21-jdk-headless ant \
 && rm -rf /var/lib/apt/lists/*
# javac reads the timer sources as UTF-8 only if the locale says so
# (testing/docker/Dockerfile).
ENV LC_ALL=C.UTF-8
COPY timer/build.xml /src/timer/build.xml
COPY timer/src /src/timer/src
COPY lib/jssc.jar lib/slf4j-nop-1.7.30.jar lib/Java-WebSocket-1.5.2.jar lib/json-20210307.jar /src/lib/
COPY website/img/status /src/website/img/status
ARG DERBYNET_VERSION=docker
RUN ant -noinput -buildfile /src/timer/build.xml -Drelease-series="$DERBYNET_VERSION" timer-jar

FROM ${UBUNTU}
# curl for the entrypoint's wait for the server, tini to pass signals on.
RUN apt-get update \
 && apt-get install -y --no-install-recommends openjdk-21-jre-headless curl tini ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=jar /src/timer/dist/lib/derby-timer.jar /usr/bin/derby-timer.jar
COPY extras/docker/timer-entrypoint.sh /usr/local/bin/derbynet-timer
# Fails the build when the platform's Java runtime is missing or broken.
RUN java -jar /usr/bin/derby-timer.jar -v

# The jar writes its timer-<date>.log in its working folder.
WORKDIR /var/lib/derbynet-timer
VOLUME /var/lib/derbynet-timer

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/derbynet-timer"]
