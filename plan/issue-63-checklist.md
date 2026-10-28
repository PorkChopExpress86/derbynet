# Ticket 63 work list

Working list for the legacy API reset and settings slice, on branch `issue-63-reset-settings-slice`. An item is checked only after its commit is pushed. Delete this file in the last commit before the PR merges, since the issue holds the record.

## Scope

- [x] `query=settings.list`: requested keys only, empty `db-connection`, no keys for an anonymous caller, `SET_UP` for every key (D5, #15, S2).
- [ ] `settings.write`: declarative table of form key, RaceInfo key, type, and checkbox flag, with every PHP failure code and the read-back check.
- [ ] `database.execute`: `schema`, `update-schema`, and `drop-message-queue-table` as a mapping, with `insecure`, `sqlnoscript`, and the `executed` and `details` keys.
- [ ] Remove fake racer images on `schema` (C28).
- [ ] `test.setup` behind `DERBYNET_ENABLE_TEST_ENDPOINTS=1`, with two-argument failures (S24, C17).
- [ ] `database.check` invariants as service functions with PHP's tags, anonymous only when the test flag is on (S26).
- [ ] `manage.py create_test_event`.
- [ ] CI sets `DERBYNET_ENABLE_TEST_ENDPOINTS=1` for the Django shell-suite job, and the developer notes say so.
- [ ] ActionLog rows keep the `settings.write` parameter names.

## Acceptance

- [ ] `test_settings_write.py`
- [x] `test_settings_list.py`
- [ ] `test_database_execute.py`
- [ ] `test_test_setup.py`
- [ ] `test_database_check.py`
- [ ] `testing/reset-database.sh` passes against Django twice in a row, and `DBCHECK=1` passes too.

## PHP defects (rows of #64)

- [ ] S2 (done), S24, S26, C17, C28
