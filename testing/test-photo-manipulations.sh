#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator

user_login_crew

curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/action.php \
    -X POST -F action=photo.upload -F photo="@`dirname $0`/data/ISO_12233-reschart.jpg" | tee $DEBUG_CURL | check_jsuccess

curl_photo_any head/file/original/ISO_12233-reschart.jpg/$RANDOM

curl_postj action.php "action=photo.assign&repo=head&photo=ISO_12233-reschart.jpg&racer=$(racerid_of 430)" | check_jsuccess

# As of PHP 5.5, this gives a different signature
# curl_photo head/file/100x100/ISO_12233-reschart.jpg/$RANDOM 16329a387284cfbb7ece2f2a6a173040
# curl_photo head/file/100x100/ISO_12233-reschart.jpg/$RANDOM 4e61175e45a4beb93aeb20973ebdc04d
# curl_photo head/file/100x100/ISO_12233-reschart.jpg/$RANDOM 0c6589fafa70aa6f44dfb054753ce347
# PHP 5.5.30:
curl_photo_any head/file/100x100/ISO_12233-reschart.jpg/$RANDOM

curl_postj action.php "action=photo.crop&repo=head&image_name=ISO_12233-reschart.jpg&left=100&top=150&right=190&bottom=270&original_height=480&original_width=900" | check_jsuccess
# curl_photo head/file/original/ISO_12233-reschart.jpg/$RANDOM b539242a66779a3721d7eb0fd6d2a9c9
# curl_photo head/file/cropped/ISO_12233-reschart.jpg/$RANDOM cd9d674652844e7f7cdd083358a4def4
# curl_photo head/file/100x100/ISO_12233-reschart.jpg/$RANDOM a6f23a5f3ba4b90b157c75cc9532bd61
# curl_photo head/file/100x100/ISO_12233-reschart.jpg/$RANDOM 5f7ff77c76740dc5ce88a100b550121e
# curl_photo head/file/100x100/ISO_12233-reschart.jpg/$RANDOM f7392996d5d93207b571a2768eea9e2e
# PHP 5.5.30:
curl_photo_any head/file/100x100/ISO_12233-reschart.jpg/$RANDOM

curl_postj action.php "action=photo.rotate&repo=head&image_name=ISO_12233-reschart.jpg&rotation=90" | check_jsuccess


curl_photo_any head/file/original/ISO_12233-reschart.jpg/$RANDOM

curl_postj action.php "action=photo.rotate&repo=head&image_name=ISO_12233-reschart.jpg&rotation=-90" | check_jsuccess
# Rotating left and then right doesn't give you back the exact same image, due to compression losses in jpg

curl_photo_any head/file/original/ISO_12233-reschart.jpg/$RANDOM
curl_photo_any head/file/cropped/ISO_12233-reschart.jpg/$RANDOM

curl_photo_any head/racer/$(racerid_of 430)/$RANDOM

# How a viewer shows the photo: the stored width x height, the EXIF orientation
# tag (or none), and the color of each quarter after applying the tag, read left
# to right and top to bottom.  Upright, pattern-o6.jpg reads RGBW (red, green
# over blue, white).
function photo_shape() {
    curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/photo.php/$1 > "$PHOTO_CURL"
    local tag=$(identify -format '%[EXIF:Orientation]' "$PHOTO_CURL" 2>/dev/null)
    echo $(identify -format '%wx%h' "$PHOTO_CURL") ${tag:-none} \
         $(convert "$PHOTO_CURL" -auto-orient -scale '2x2!' -depth 8 txt:- | \
               awk -F'[(),]' 'NR > 1 { r = $3; g = $4; b = $5;
                   printf "%s", (r > 150 && g > 150 && b > 150) ? "W" : (r > g && r > b) ? "R" : (g > b) ? "G" : "B" }')
}

# expect_photo_shape $1=url_tail $2="WIDTHxHEIGHT TAG QUARTERS"
function expect_photo_shape() {
    echo ' ' ' ' ' ' photo_shape $1 expected to be $2 >&2
    local actual=$(photo_shape $1)
    echo photo $1 $actual >> $OUTPUT_CURL
    [ "$actual" = "$2" ] || test_fails Photo $1 is "$actual", expected "$2"
}

PHOTO_CURL="`dirname $0`/photo.curl"
PATTERN_O6="`dirname $0`/data/carphotos/pattern-o6.jpg"

# A phone photo taken in portrait: stored 320x480 with EXIF orientation 6, upright 480x320.
curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/action.php \
    -X POST -F action=photo.upload -F repo=car -F photo="@$PATTERN_O6" | tee $DEBUG_CURL | check_jsuccess

curl_photo car/file/original/pattern-o6.jpg/$RANDOM $(md5_digest < "$PATTERN_O6")
expect_photo_shape car/file/original/pattern-o6.jpg/$RANDOM "320x480 6 RGBW"

if [ "$DERBYNET_BACKEND" = django ] ; then
    expect_photo_shape car/file/200x200/pattern-o6.jpg/$RANDOM "200x133 none RGBW"
    expect_photo_shape car/file/work/pattern-o6.jpg/$RANDOM "600x400 none RGBW"
else
    # Known PHP bug that Django fixes (#73): GD renders the stored pixels and
    # drops the tag, so the renders come out turned left, with the size transposed.
    expect_photo_shape car/file/200x200/pattern-o6.jpg/$RANDOM "133x200 none GWRB"
    expect_photo_shape car/file/work/pattern-o6.jpg/$RANDOM "400x600 none GWRB"
fi

# The suite runs this script twice, and PHP renames a second upload of the same name.
curl_postj action.php "action=photo.delete&repo=car&photo=pattern-o6.jpg" | check_jsuccess
