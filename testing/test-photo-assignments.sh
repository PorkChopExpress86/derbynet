#! /bin/bash

# This only works when run on the server machine, not a separate client

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

# Usage: test-photo-assignments.sh <base_url> basic|master
#
# Two racers have different car numbers in the two databases this runs against:
# test-basic-racing.sh renumbers them, and test-master-schedule.sh doesn't.
if [ "$2" = basic ] ; then
    CAR_5=999
    CAR_39=139
else
    CAR_5=405
    CAR_39=508
fi

user_login_coordinator

curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 101)&photo=Car-1234.jpg" | check_jfailure
# For demo purposes, we want at least one car photo assigned early in the process
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 101)&photo=Car-1637.jpg" | check_jsuccess

curl_postj action.php "action=photo.assign&racer=$(racerid_of 101)&photo=head-A.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 202)&photo=head-B.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 303)&photo=Cub-8759.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 501)&photo=Cub-2825.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of $CAR_5)&photo=Cub-6429.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 106)&photo=head-D.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 207)&photo=Cub-1142.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 308)&photo=Cub-32106.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 502)&photo=Cub-7145.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 410)&photo=Cub-1612.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 111)&photo=head-C.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 212)&photo=Cub-0117.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 313)&photo=Cub-2878.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 503)&photo=Cub-0707.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 415)&photo=Cub-7530.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 116)&photo=head-E.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 217)&photo=Cub-0033.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 318)&photo=Cub-1809.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 504)&photo=Cub-9652.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 420)&photo=Cub-5312.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 121)&photo=head-F.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 222)&photo=Cub-2995.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 323)&photo=Cub-7962.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 505)&photo=Cub-8200.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 425)&photo=Cub-7232.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 126)&photo=head-G.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 227)&photo=Cub-0087.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 328)&photo=Cub-2961.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 506)&photo=Cub-6155.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 430)&photo=Cub-7348.jpg" | check_jsuccess
# See test-slide-next.sh:
# curl_postj action.php "action=photo.assign&racer=31&photo=Cub-9634.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 232)&photo=Cub-1478.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 333)&photo=Cub-0764.jpg" | check_jsuccess
# curl_postj action.php "action=photo.assign&racer=34&photo=Cub-5517.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 435)&photo=Cub-8464.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&racer=$(racerid_of 136)&photo=head-H.jpg" | check_jsuccess

curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 237)&photo=Cub-0330.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 338)&photo=Cub-6494.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of $CAR_39)&photo=Cub-3126.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 440)&photo=Cub-4356.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 141)&photo=head-I.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 242)&photo=Cub-6712.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 343)&photo=Cub-6040.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 509)&photo=Cub-4268.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 445)&photo=Cub-7559.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 146)&photo=head-J.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 247)&photo=Cub-1280.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 348)&photo=Cub-2418.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 510)&photo=Cub-3714.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 450)&photo=Cub-4622.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 151)&photo=head-K.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 252)&photo=Cub-9408.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 353)&photo=Cub-0315.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 511)&photo=Cub-4708.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 455)&photo=Cub-0543.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 156)&photo=head-L.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 257)&photo=Cub-1376.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 358)&photo=Cub-4352.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 512)&photo=Cub-1451.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 460)&photo=Cub-6386.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 161)&photo=head-M.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 262)&photo=Cub-3389.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 363)&photo=Cub-1547.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 513)&photo=Cub-0076.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 465)&photo=Cub-8724.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 166)&photo=head-N.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 267)&photo=Cub-8590.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 368)&photo=Cub-7883.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 514)&photo=Cub-0533.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 470)&photo=Cub-8188.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 171)&photo=head-O.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 272)&photo=Cub-4411.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 373)&photo=Cub-3262.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 515)&photo=Cub-4905.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 475)&photo=Cub-9555.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 176)&photo=head-P.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 277)&photo=Cub-0435.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 378)&photo=Cub-4863.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 516)&photo=Cub-0324.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 480)&photo=Cub-8330.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 181)&photo=head-Q.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=head&racer=$(racerid_of 282)&photo=Cub-6507.jpg" | check_jsuccess
# curl_postj action.php "action=photo.assign&repo=head&racer=83&photo=Cub-7261.jpg" | check_jsuccess


curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 101)&photo=Car-1637.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 202)&photo=Car-1638.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 303)&photo=Car-1639.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 501)&photo=Car-1640.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of $CAR_5)&photo=Car-1641.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 106)&photo=Car-1642.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 207)&photo=Car-1643.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 308)&photo=Car-1644.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 502)&photo=Car-1645.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 410)&photo=Car-1646.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 111)&photo=Car-1647.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 212)&photo=Car-1648.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 313)&photo=Car-1649.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 503)&photo=Car-1650.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 415)&photo=Car-1651.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 116)&photo=Car-1652.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 217)&photo=Car-1688.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 318)&photo=Car-1845.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 504)&photo=Car-1848.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 420)&photo=Car-1849.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 121)&photo=Car-1850.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 222)&photo=Car-1851.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 323)&photo=Car-1852.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 505)&photo=Car-1853.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 425)&photo=Car-1854.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 126)&photo=Car-1855.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 227)&photo=Car-1856.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 328)&photo=Car-1857.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 506)&photo=Car-1858.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 430)&photo=Car-1859.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 131)&photo=Car-1860.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 232)&photo=Car-1861.jpg" | check_jsuccess
# See test-slide-next.sh
# curl_postj action.php "action=photo.assign&repo=car&racer=33&photo=Car-1862.jpg" | check_jsuccess
# curl_postj action.php "action=photo.assign&repo=car&racer=34&photo=Car-1863.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 435)&photo=Car-1864.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 136)&photo=Car-1865.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 237)&photo=Car-1866.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 338)&photo=Car-1867.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of $CAR_39)&photo=Car-1868.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 440)&photo=Car-1869.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 141)&photo=Car-1870.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 242)&photo=Car-1871.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 343)&photo=Car-1872.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 509)&photo=Car-1873.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 445)&photo=Car-1874.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 146)&photo=Car-1875.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 247)&photo=Car-1876.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 348)&photo=Car-1877.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 510)&photo=Car-1878.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 450)&photo=Car-1879.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 151)&photo=Car-1880.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 252)&photo=Car-1881.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 353)&photo=Car-1882.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 511)&photo=Car-1883.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 455)&photo=Car-5611.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 156)&photo=Car-5612.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 257)&photo=Car-5613.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 358)&photo=Car-5614.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 512)&photo=Car-5615.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 460)&photo=Car-5616.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 161)&photo=Car-5618.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 262)&photo=Car-5619.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 363)&photo=Car-5620.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 513)&photo=Car-5621.jpg" | check_jsuccess
# These photo assignments should have already occurred in photo-setup.sh
# curl_postj action.php "action=photo.assign&repo=car&racer=65&photo=Car-5622.jpg" | check_jsuccess
# curl_postj action.php "action=photo.assign&repo=car&racer=66&photo=Car-5623.jpg" | check_jsuccess
# curl_postj action.php "action=photo.assign&repo=car&racer=67&photo=Car-5624.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 368)&photo=Car-5625.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 514)&photo=Car-5626.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 470)&photo=Car-5627.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 171)&photo=Car-5628.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 272)&photo=Car-5629.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 373)&photo=Car-5630.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 515)&photo=Car-5631.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 475)&photo=Car-5632.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 176)&photo=Car-5633.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 277)&photo=Car-5634.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 378)&photo=Car-5635.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 516)&photo=Car-5636.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 480)&photo=Car-5637.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 181)&photo=Car-5638.jpg" | check_jsuccess
curl_postj action.php "action=photo.assign&repo=car&racer=$(racerid_of 282)&photo=Car-5639.jpg" | check_jsuccess
# curl_postj action.php "action=photo.assign&repo=car&racer=83&photo=Car-7261.jpg" | check_jsuccess


## Once this runs, the file links in the database become invalid
## TODO rm -rf "$PHOTO_DIR" "$CAR_PHOTO_DIR"
