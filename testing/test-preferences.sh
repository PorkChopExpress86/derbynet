#! /bin/bash

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

user_login_coordinator


RESET_SOURCE=preferences `dirname $0`/reset-database.sh "$BASE_URL"

curl --location -s -b $COOKIES_CURL -c $COOKIES_CURL $BASE_URL/action.php \
     -X POST \
     -F action=preferences.upload \
     -F prefs="@-;filename=example.pref" \
     -F MAX_FILE_SIZE=2000000 <<EOF \
    | tee $DEBUG_CURL | check_jsuccess
# Preferences written 2024-04-27

## Groups
GROUP = Lions & Tigers
  SEGMENT = Lion
  SEGMENT = Lion Pro-Stock
  SEGMENT = Tiger
  SEGMENT = Tiger Pro-Stock
ENDGROUP
GROUP = Webelos I and II
  SEGMENT = Webelos
  SEGMENT = Webelos Pro-Stock
  SEGMENT = Arrow of Light
  SEGMENT = Arrow of Light Pro-Stock
ENDGROUP
GROUP = Wolves & Bears
  SEGMENT = Bear
  SEGMENT = Bear Pro-Stock
  SEGMENT = Wolf
  SEGMENT = Wolf Pro-Stock
ENDGROUP

## Awards

## Settings
n-lanes = 4
group-formation-rule = custom
use-subgroups
partition-label = Rank
supergroup-label = Council
n-pack-trophies = 0
n-den-trophies = 0
n-rank-trophies = 3
racing_scene = 4
warn-no-timer

## Aggregates
AGGREGATE = All Lions
  CONSTITUENTSUBGROUP = Lion
  CONSTITUENTSUBGROUP = Lion Pro-Stock
ENDAGGREGATE
AGGREGATE = All Tigers
  CONSTITUENTSUBGROUP = Tiger
  CONSTITUENTSUBGROUP = Tiger Pro-Stock
ENDAGGREGATE
AGGREGATE = All Wolves
  CONSTITUENTSUBGROUP = Wolf
  CONSTITUENTSUBGROUP = Wolf Pro-Stock
ENDAGGREGATE
AGGREGATE = All Bears
  CONSTITUENTSUBGROUP = Bear
  CONSTITUENTSUBGROUP = Bear Pro-Stock
ENDAGGREGATE
AGGREGATE = All Webelos
  CONSTITUENTSUBGROUP = Webelos
  CONSTITUENTSUBGROUP = Webelos Pro-Stock
ENDAGGREGATE
AGGREGATE = All Arrow-of-Light
  CONSTITUENTSUBGROUP = Arrow of Light
  CONSTITUENTSUBGROUP = Arrow of Light Pro-Stock
ENDAGGREGATE

EOF


# TODO
curl_getj "action.php?query=poll.coordinator" > "${TMPDIR:-/tmp}/poll-coordinator.json"

# curl_getj "action.php?query=poll&values=awards,classes,partitions,race-structure,rounds"

diff <(curl_getj "action.php?query=poll&values=classes,partitions,rounds" | normalize_ids) - <<'EOF'
{
  "classes": [
    {
      "classid": "class#1",
      "count": 0,
      "nrounds": 0,
      "ntrophies": -1,
      "name": "Lions & Tigers",
      "subgroups": [
        {
          "rankid": "rank#1",
          "count": 0,
          "ntrophies": -1,
          "name": "Lion"
        },
        {
          "rankid": "rank#2",
          "count": 0,
          "ntrophies": -1,
          "name": "Lion Pro-Stock"
        },
        {
          "rankid": "rank#3",
          "count": 0,
          "ntrophies": -1,
          "name": "Tiger"
        },
        {
          "rankid": "rank#4",
          "count": 0,
          "ntrophies": -1,
          "name": "Tiger Pro-Stock"
        }
      ]
    },
    {
      "classid": "class#2",
      "count": 0,
      "nrounds": 0,
      "ntrophies": -1,
      "name": "Webelos I and II",
      "subgroups": [
        {
          "rankid": "rank#5",
          "count": 0,
          "ntrophies": -1,
          "name": "Webelos"
        },
        {
          "rankid": "rank#6",
          "count": 0,
          "ntrophies": -1,
          "name": "Webelos Pro-Stock"
        },
        {
          "rankid": "rank#7",
          "count": 0,
          "ntrophies": -1,
          "name": "Arrow of Light"
        },
        {
          "rankid": "rank#8",
          "count": 0,
          "ntrophies": -1,
          "name": "Arrow of Light Pro-Stock"
        }
      ]
    },
    {
      "classid": "class#3",
      "count": 0,
      "nrounds": 0,
      "ntrophies": -1,
      "name": "Wolves & Bears",
      "subgroups": [
        {
          "rankid": "rank#9",
          "count": 0,
          "ntrophies": -1,
          "name": "Bear"
        },
        {
          "rankid": "rank#10",
          "count": 0,
          "ntrophies": -1,
          "name": "Bear Pro-Stock"
        },
        {
          "rankid": "rank#11",
          "count": 0,
          "ntrophies": -1,
          "name": "Wolf"
        },
        {
          "rankid": "rank#12",
          "count": 0,
          "ntrophies": -1,
          "name": "Wolf Pro-Stock"
        }
      ]
    },
    {
      "classid": "class#4",
      "count": 0,
      "nrounds": 0,
      "ntrophies": -1,
      "name": "All Lions",
      "aggregate-by-subgroup": true,
      "subgroups": [],
      "constituents": [
        {
          "classid": "class#1",
          "name": "Lions & Tigers"
        }
      ],
      "constituent-ranks": [
        {
          "rankid": "rank#1",
          "name": "Lion",
          "classid": "class#1",
          "class": "Lions & Tigers"
        },
        {
          "rankid": "rank#2",
          "name": "Lion Pro-Stock",
          "classid": "class#1",
          "class": "Lions & Tigers"
        }
      ]
    },
    {
      "classid": "class#5",
      "count": 0,
      "nrounds": 0,
      "ntrophies": -1,
      "name": "All Tigers",
      "aggregate-by-subgroup": true,
      "subgroups": [],
      "constituents": [
        {
          "classid": "class#1",
          "name": "Lions & Tigers"
        }
      ],
      "constituent-ranks": [
        {
          "rankid": "rank#3",
          "name": "Tiger",
          "classid": "class#1",
          "class": "Lions & Tigers"
        },
        {
          "rankid": "rank#4",
          "name": "Tiger Pro-Stock",
          "classid": "class#1",
          "class": "Lions & Tigers"
        }
      ]
    },
    {
      "classid": "class#6",
      "count": 0,
      "nrounds": 0,
      "ntrophies": -1,
      "name": "All Wolves",
      "aggregate-by-subgroup": true,
      "subgroups": [],
      "constituents": [
        {
          "classid": "class#3",
          "name": "Wolves & Bears"
        }
      ],
      "constituent-ranks": [
        {
          "rankid": "rank#11",
          "name": "Wolf",
          "classid": "class#3",
          "class": "Wolves & Bears"
        },
        {
          "rankid": "rank#12",
          "name": "Wolf Pro-Stock",
          "classid": "class#3",
          "class": "Wolves & Bears"
        }
      ]
    },
    {
      "classid": "class#7",
      "count": 0,
      "nrounds": 0,
      "ntrophies": -1,
      "name": "All Bears",
      "aggregate-by-subgroup": true,
      "subgroups": [],
      "constituents": [
        {
          "classid": "class#3",
          "name": "Wolves & Bears"
        }
      ],
      "constituent-ranks": [
        {
          "rankid": "rank#9",
          "name": "Bear",
          "classid": "class#3",
          "class": "Wolves & Bears"
        },
        {
          "rankid": "rank#10",
          "name": "Bear Pro-Stock",
          "classid": "class#3",
          "class": "Wolves & Bears"
        }
      ]
    },
    {
      "classid": "class#8",
      "count": 0,
      "nrounds": 0,
      "ntrophies": -1,
      "name": "All Webelos",
      "aggregate-by-subgroup": true,
      "subgroups": [],
      "constituents": [
        {
          "classid": "class#2",
          "name": "Webelos I and II"
        }
      ],
      "constituent-ranks": [
        {
          "rankid": "rank#5",
          "name": "Webelos",
          "classid": "class#2",
          "class": "Webelos I and II"
        },
        {
          "rankid": "rank#6",
          "name": "Webelos Pro-Stock",
          "classid": "class#2",
          "class": "Webelos I and II"
        }
      ]
    },
    {
      "classid": "class#9",
      "count": 0,
      "nrounds": 0,
      "ntrophies": -1,
      "name": "All Arrow-of-Light",
      "aggregate-by-subgroup": true,
      "subgroups": [],
      "constituents": [
        {
          "classid": "class#2",
          "name": "Webelos I and II"
        }
      ],
      "constituent-ranks": [
        {
          "rankid": "rank#7",
          "name": "Arrow of Light",
          "classid": "class#2",
          "class": "Webelos I and II"
        },
        {
          "rankid": "rank#8",
          "name": "Arrow of Light Pro-Stock",
          "classid": "class#2",
          "class": "Webelos I and II"
        }
      ]
    }
  ],
  "partitions": [
    {
      "partitionid": "partition#1",
      "name": "Lion",
      "sortorder": 1,
      "count": 0,
      "classids": [
        "class#1"
      ],
      "rankids": [
        "rank#1"
      ]
    },
    {
      "partitionid": "partition#2",
      "name": "Lion Pro-Stock",
      "sortorder": 2,
      "count": 0,
      "classids": [
        "class#1"
      ],
      "rankids": [
        "rank#2"
      ]
    },
    {
      "partitionid": "partition#3",
      "name": "Tiger",
      "sortorder": 3,
      "count": 0,
      "classids": [
        "class#1"
      ],
      "rankids": [
        "rank#3"
      ]
    },
    {
      "partitionid": "partition#4",
      "name": "Tiger Pro-Stock",
      "sortorder": 4,
      "count": 0,
      "classids": [
        "class#1"
      ],
      "rankids": [
        "rank#4"
      ]
    },
    {
      "partitionid": "partition#5",
      "name": "Webelos",
      "sortorder": 5,
      "count": 0,
      "classids": [
        "class#2"
      ],
      "rankids": [
        "rank#5"
      ]
    },
    {
      "partitionid": "partition#6",
      "name": "Webelos Pro-Stock",
      "sortorder": 6,
      "count": 0,
      "classids": [
        "class#2"
      ],
      "rankids": [
        "rank#6"
      ]
    },
    {
      "partitionid": "partition#7",
      "name": "Arrow of Light",
      "sortorder": 7,
      "count": 0,
      "classids": [
        "class#2"
      ],
      "rankids": [
        "rank#7"
      ]
    },
    {
      "partitionid": "partition#8",
      "name": "Arrow of Light Pro-Stock",
      "sortorder": 8,
      "count": 0,
      "classids": [
        "class#2"
      ],
      "rankids": [
        "rank#8"
      ]
    },
    {
      "partitionid": "partition#9",
      "name": "Bear",
      "sortorder": 9,
      "count": 0,
      "classids": [
        "class#3"
      ],
      "rankids": [
        "rank#9"
      ]
    },
    {
      "partitionid": "partition#10",
      "name": "Bear Pro-Stock",
      "sortorder": 10,
      "count": 0,
      "classids": [
        "class#3"
      ],
      "rankids": [
        "rank#10"
      ]
    },
    {
      "partitionid": "partition#11",
      "name": "Wolf",
      "sortorder": 11,
      "count": 0,
      "classids": [
        "class#3"
      ],
      "rankids": [
        "rank#11"
      ]
    },
    {
      "partitionid": "partition#12",
      "name": "Wolf Pro-Stock",
      "sortorder": 12,
      "count": 0,
      "classids": [
        "class#3"
      ],
      "rankids": [
        "rank#12"
      ]
    }
  ],
  "rounds": [
    {
      "roundid": "round#1",
      "classid": "class#1",
      "class": "Lions & Tigers",
      "round": 1,
      "aggregate": false,
      "roster_size": 0,
      "passed": 0,
      "unscheduled": 0,
      "adjustments": [],
      "heats_scheduled": 0,
      "heats_run": 0,
      "name": "Lions & Tigers, Round 1"
    },
    {
      "roundid": "round#2",
      "classid": "class#2",
      "class": "Webelos I and II",
      "round": 1,
      "aggregate": false,
      "roster_size": 0,
      "passed": 0,
      "unscheduled": 0,
      "adjustments": [],
      "heats_scheduled": 0,
      "heats_run": 0,
      "name": "Webelos I and II, Round 1"
    },
    {
      "roundid": "round#3",
      "classid": "class#3",
      "class": "Wolves & Bears",
      "round": 1,
      "aggregate": false,
      "roster_size": 0,
      "passed": 0,
      "unscheduled": 0,
      "adjustments": [],
      "heats_scheduled": 0,
      "heats_run": 0,
      "name": "Wolves & Bears, Round 1"
    }
  ]
}
EOF
