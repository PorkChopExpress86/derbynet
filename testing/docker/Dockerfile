## Tools for building the PHP assets and running the shell test suite.
## Build context is testing/puppeteer, which supplies the npm lock file:
##   docker build -t derbynet_test_harness \
##       -f testing/docker/Dockerfile testing/puppeteer

FROM node:22-bookworm

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ant default-jdk-headless jq libxml2-utils \
        fonts-liberation libasound2 libatk-bridge2.0-0 libatk1.0-0 libcairo2 \
        libcups2 libdbus-1-3 libdrm2 libgbm1 libnspr4 libnss3 libpango-1.0-0 \
        libx11-xcb1 libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 \
        libxrandr2 && \
    rm -rf /var/lib/apt/lists/*

# The container runs as the invoking user, whose checkout is owned by someone else.
RUN git config --system safe.directory '*'

ENV NODE_PATH=/opt/derbynet-test/node_modules
ENV PUPPETEER_CACHE_DIR=/opt/derbynet-test/puppeteer-cache
COPY package.json package-lock.json /opt/derbynet-test/
RUN cd /opt/derbynet-test && npm ci && chmod -R a+rX /opt/derbynet-test

# Chrome's sandbox can't start inside a container.  The tests call
# puppeteer.launch() without arguments, so supply the flag with a wrapper.
RUN CHROME=$(find /opt/derbynet-test/puppeteer-cache -name chrome -type f | head -n 1) && \
    printf '#!/bin/sh\nexec %s --no-sandbox "$@"\n' "$CHROME" > /usr/local/bin/chrome-no-sandbox && \
    chmod 755 /usr/local/bin/chrome-no-sandbox
ENV PUPPETEER_EXECUTABLE_PATH=/usr/local/bin/chrome-no-sandbox

# javac reads the timer sources as UTF-8 only if the locale says so.
ENV LC_ALL=C.UTF-8

WORKDIR /derbynet
