# syntax=docker/dockerfile:1.7
#
# The photo stand image (#59): extras/scripts/photostand.sh with gphoto2 and
# fswebcam, for a Raspberry Pi with a barcode scanner and a camera.  The
# scanner comes in through /dev/input and the camera through /dev/bus/usb
# (compose.photostand.yaml).  chdkptp is not in the image: a CHDK camera's
# owner mounts their own copy (docs/pi-setup.md).  The build context is the
# repository root:
#
#   docker buildx build --platform linux/arm64 -f extras/docker/photostand.Dockerfile -t derbynet_photostand .
#
# docs/pi-setup.md says how to run it.

ARG UBUNTU=ubuntu:26.04@sha256:065aa8626841f16057b78b027b464cd59c4fac36c204c72dcbf48d66f5fa8a96

FROM ${UBUNTU}
# photostand.sh and its lib/ call curl, ip (iproute2), GNU date and stat, and
# python3 runs read-barcodes.  flite and mpg123 speak and play its sounds
# through /dev/snd, so a stand with no screen still says what it is doing.
# tini passes signals on.  libusb-0.1-4 is the USB library a chdkptp build
# mounted at /opt/chdkptp needs (docs/pi-setup.md).
RUN apt-get update \
 && apt-get install -y --no-install-recommends gphoto2 fswebcam flite mpg123 \
      curl iproute2 python3 tini ca-certificates libusb-0.1-4 \
 && rm -rf /var/lib/apt/lists/*
# mpg123 keeps only its ALSA output.  With no sound card, mpg123 with all its
# outputs ran on one sound for over 5 minutes and held the stand (#59's
# check under QEMU); with ALSA alone it fails in under a second.
RUN find /usr/lib/*/mpg123 -name 'output_*.so' ! -name output_alsa.so -delete \
 && ls /usr/lib/*/mpg123/
# The places the derbynet-extras package put them, which the scripts name.
COPY extras/scripts/photostand.sh /usr/share/derbynet/scripts/photostand.sh
COPY extras/scripts/lib /usr/share/derbynet/scripts/lib
COPY extras/conf /usr/share/derbynet/conf
COPY extras/sounds /usr/share/derbynet/sounds
COPY extras/docker/read-barcodes.py /usr/local/bin/read-barcodes
COPY extras/docker/photostand-entrypoint.sh /usr/local/bin/derbynet-photostand
# Fails the build when a camera tool is missing for the platform.
RUN gphoto2 --version > /dev/null \
 && command -v fswebcam flite mpg123 ip curl

# photostand.sh keeps uploads.log, checkins.log and the photos-<date> folders
# in its working folder.
WORKDIR /var/lib/derbynet-photostand
VOLUME /var/lib/derbynet-photostand

ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/derbynet-photostand"]
