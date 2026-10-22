# Replaces every database ID in a JSON response with a label that depends only on
# where the ID first appears: the first class ID in the document becomes "class#1",
# the second one "class#2", and so on.  Two responses that have the same shape and
# refer to the same things in the same places come out equal, whatever IDs the
# server assigned.  See normalize_ids in common.sh.

def idkind:
  . as $p
  | ($p[-1]) as $last
  | if ($last | type) == "string" then
      {"classid": "class", "rankid": "rank", "partitionid": "partition",
       "roundid": "round", "racerid": "racer", "awardid": "award"}[$last]
    else
      {"classids": "class", "rankids": "rank", "partitionids": "partition"}
        [($p[-2] // "") | tostring]
    end;

. as $doc
| [paths(scalars) | select(idkind != null)] as $idpaths
| (reduce $idpaths[] as $p ({};
     ($p | idkind) as $k
     | ($doc | getpath($p)) as $v
     | if (.[$k] // []) | index([$v]) then . else .[$k] += [$v] end)) as $seen
| reduce $idpaths[] as $p ($doc;
    ($p | idkind) as $k
    | setpath($p; $k + "#" + (($seen[$k] | index([$doc | getpath($p)])) + 1 | tostring)))
