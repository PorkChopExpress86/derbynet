#!/bin/sh
# Writes the PHP side of the import_roles tests: the file PHP's installer
# writes, that file after the Debian postinst patch, and json_encode() of each
# input's $roles. Run from the repository root:
#
#   docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/website:/website:ro" \
#     -v "$PWD/server/accounts/tests/data:/out" php:8.3-cli /bin/sh /out/capture-php.sh
set -e
cd /website
DERBYNET_CONFIG_DIR=/out php -r 'require "inc/write-config-files.inc"; exit(write_config_roles_file("config-roles-default.inc") ? 0 : 1);'
cp /out/config-roles-default.inc /out/config-roles-postinst.inc
# The line from installer/debian/server/postinst, as it runs on every upgrade.
grep -q -v JUDGING_PERMISSION /out/config-roles-postinst.inc && \
    sed -i -e 's/ASSIGN_RACER_IMAGE_PERMISSION/ASSIGN_RACER_IMAGE_PERMISSION | JUDGING_PERMISSION/' /out/config-roles-postinst.inc
for f in default postinst hand-edited; do
  php -r 'require "inc/permissions.inc"; include $argv[1]; echo json_encode(["roles" => $roles, "post_setup_role" => $post_setup_role]), "\n";' /out/config-roles-$f.inc > /out/config-roles-$f.json
done
php --version | head -1
