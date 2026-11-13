#! /bin/bash
#
# Pushes the image the build job made and the test jobs ran (#58), both
# platforms from its OCI archive, with the same digests, to a registry.
# .github/workflows/image.yml runs it against the build job's own registry as
# a dry run, and against ghcr.io on a release tag (D89).
#
# Usage: server/docker/ci/publish.sh <oci archive> <repository> <tag> [skopeo options]
#
# The tag is a release tag, v1.2.3 or a prerelease such as v1.2.3-rc1.  A
# release is also pushed as `latest`; a prerelease is not.  Options after the
# tag go to `skopeo copy`, such as --dest-tls-verify=false for a local
# registry.

set -euo pipefail

archive="${1:?usage: publish.sh <oci archive> <repository> <tag> [skopeo options]}"
repository="${2:?usage: publish.sh <oci archive> <repository> <tag> [skopeo options]}"
tag="${3:?usage: publish.sh <oci archive> <repository> <tag> [skopeo options]}"
shift 3

[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]] || {
    echo "publish.sh: $tag is not a release tag such as v1.2.3 or v1.2.3-rc1" >&2
    exit 1
}
tags=("$tag")
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] && tags+=(latest)

for each in "${tags[@]}"; do
    echo "pushing $repository:$each"
    skopeo copy --all --preserve-digests "$@" "oci-archive:$archive" "docker://$repository:$each"
done
