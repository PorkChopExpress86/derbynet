#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh


curl_postj action.php "action=database.purge&purge=schedules" | check_jsuccess
curl_postj action.php "action=racer.bulk&who=all&what=checkin&value=1" | check_jsuccess

curl_postj action.php "action=test.setup&slide=slide1.png" | check_jsuccess
curl_postj action.php "action=test.setup&slide=slide2.png" | check_jsuccess
curl_postj action.php "action=test.setup&subdir=alt&slide=alt1.png" | check_jsuccess
curl_postj action.php "action=test.setup&subdir=alt&slide=alt2.png" | check_jsuccess

curl_getj "action.php?query=slide.next" | \
    jq '    (.photo.photo | test("slide.php/title"))' | \
    expect_eq true

curl_getj "action.php?query=slide.next&mode=slide" | \
    jq '    (.photo.photo | test("slide.php/slide1"))' | \
    expect_eq true

curl_getj "action.php?query=slide.next&mode=slide&file=slide1.png" | \
    jq '    (.photo.photo | test("slide.php/slide2"))' | \
    expect_eq true

curl_getj "action.php?query=slide.next&mode=slide&file=slide2.png" | \
    jq --argjson car101 $(racerid_of 101) '    (.photo.photo | test("photo.php/car/file/cropped/Car-1637.jpg/.*"))
        and (.photo.inset | test("photo.php/head/file/cropped/head-A.jpg/.*"))
        and (.photo.next.mode == "racer")
        and (.photo.next.racerid == $car101)' | \
    expect_eq true

curl_getj "action.php?query=slide.next&mode=racer&racerid=0" | \
    jq --argjson car101 $(racerid_of 101) '    (.photo.photo | test("photo.php/car/file/cropped/Car-1637.jpg/.*"))
        and (.photo.inset | test("photo.php/head/file/cropped/head-A.jpg/.*"))
        and (.photo.next.mode == "racer")
        and (.photo.next.racerid == $car101)' | \
    expect_eq true

# Restrictions by classid:
# Lions & Tigers has Car-1637, then Car-1642
# White's Wolves has Car-1638, then Car-1643
# Bears & Freres has Car-1639, then Car-1644
curl_getj "action.php?query=slide.next&mode=racer&racerid=0&classids=$(classid_of White)" | \
    jq --argjson car202 $(racerid_of 202) '    (.photo.photo | test("photo.php/car/file/cropped/Car-1638.jpg/.*"))
        and (.photo.next.mode == "racer")
        and (.photo.next.racerid == $car202)' | \
    expect_eq true

curl_getj "action.php?query=slide.next&mode=racer&racerid=0&classids=$(classid_of Bears)" | \
    jq --argjson car303 $(racerid_of 303) '    (.photo.photo | test("photo.php/car/file/cropped/Car-1639.jpg/.*"))
        and (.photo.next.mode == "racer")
        and (.photo.next.racerid == $car303)' | \
    expect_eq true

curl_getj "action.php?query=slide.next&mode=racer&racerid=0&classids=$(classid_of White),$(classid_of Bears)" | \
    jq --argjson car202 $(racerid_of 202) '    (.photo.photo | test("photo.php/car/file/cropped/Car-1638.jpg/.*"))
        and (.photo.next.mode == "racer")
        and (.photo.next.racerid == $car202)' | \
    expect_eq true

curl_getj "action.php?query=slide.next&mode=racer&racerid=$(racerid_of 415)" | \
    jq --argjson car116 $(racerid_of 116) '    (.photo.photo | test("photo.php/car/file/cropped/Car-1652.jpg/.*"))
        and (.photo.inset | test("photo.php/head/file/cropped/head-E.jpg/.*"))
        and (.photo.next.mode == "racer")
        and (.photo.next.racerid == $car116)' | \
    expect_eq true

# Racerid 32 (Freddie Font) has car but no head shot
curl_getj "action.php?query=slide.next&mode=racer&racerid=$(racerid_of 131)" | \
    jq --argjson car232 $(racerid_of 232) '    (.photo.photo | test("photo.php/car/file/cropped/Car-1861.jpg/.*"))
        and (.photo.inset | test("photo.php/head/file/cropped/Cub-1478.jpg/.*"))
        and (.photo.next.mode == "racer")
        and (.photo.next.racerid == $car232)' | \
    expect_eq true

# Racerid 33 (Freeman Fizer) has head shot but no car photo
curl_getj "action.php?query=slide.next&mode=racer&racerid=$(racerid_of 232)" | \
    jq --argjson car333 $(racerid_of 333) '    (.photo.photo | test("photo.php/head/file/cropped/Cub-0764.jpg/.*"))
        and (.photo.inset? | not)
        and (.photo.next.mode == "racer")
        and (.photo.next.racerid == $car333)' | \
    expect_eq true

# Racerid 34 (Gregg Grove) has neither; get racer 35 instead
curl_getj "action.php?query=slide.next&mode=racer&racerid=$(racerid_of 333)" | \
    jq --argjson car435 $(racerid_of 435) '    (.photo.photo | test("photo.php/car/file/cropped/Car-1864.jpg/.*"))
        and (.photo.inset | test("photo.php/head/file/cropped/Cub-8464.jpg/.*"))
        and (.photo.carnumber == 435)
        and (.photo.name == "Harley Howell")
        and (.photo.next.mode == "racer")
        and (.photo.next.racerid == $car435)' | \
    expect_eq true

curl_getj "action.php?query=slide.next&mode=racer&racerid=$(racerid_of 181)" |
    jq --argjson car282 $(racerid_of 282) '    (.photo.photo | test("photo.php/car/file/cropped/Car-5639.jpg/.*"))
        and (.photo.inset | test("photo.php/head/file/cropped/Cub-6507.jpg/.*"))
        and (.photo.name == "Willard Woolfolk")
        and (.photo.next.mode == "racer")
        and (.photo.next.racerid == $car282)' | \
    expect_eq true

curl_getj "action.php?query=slide.next&mode=racer&racerid=$(racerid_of 282)" |
    jq '    (.photo.photo == "slide.php/title")
        and (.photo.title == true)
        and (.photo.next.mode == "slide")
        and (.photo.next.file == "")' | \
    expect_eq true

curl_getj "action.php?query=slide.next&mode=slide&file=zzz" |
    jq --argjson car101 $(racerid_of 101) '    (.photo.photo | test("photo.php/car/file/cropped/Car-1637.jpg/.*"))
        and (.photo.inset | test("photo.php/head/file/cropped/head-A.jpg/.*"))
        and (.photo.next.mode == "racer")
        and (.photo.next.racerid == $car101)' | \
    expect_eq true

# Alt slideshow
curl_getj "action.php?query=slide.next&subdir=alt" | \
    jq '    (.photo.photo | test("slide.php/alt/title"))' | \
    expect_eq true

curl_getj "action.php?query=slide.next&subdir=alt&mode=slide" | \
    jq '    (.photo.photo | test("slide.php/alt/alt1.png"))' | \
    expect_eq true

curl_getj "action.php?query=slide.next&subdir=alt&mode=slide&file=alt1.png" | \
    jq '    (.photo.photo | test("slide.php/alt/alt2.png"))' | \
    expect_eq true

curl_getj "action.php?query=slide.next&subdir=alt&mode=slide&file=alt2.png" | \
    jq --argjson car101 $(racerid_of 101) '    (.photo.photo | test("photo.php/car/file/cropped/Car-1637.jpg/.*"))
        and (.photo.inset | test("photo.php/head/file/cropped/head-A.jpg/.*"))
        and (.photo.next.mode == "racer")
        and (.photo.next.racerid == $car101)' | \
    expect_eq true
