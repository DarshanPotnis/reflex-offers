# Data protection

Invalid quantities and prices sent straight to the API, bypassing the screen
entirely. Every one is rejected with a message a person can act on, and the
saved offer is left **byte-for-byte identical**.

## Setup

```bash
cd backend
env -u DATABASE_URL DATABASE_URL="sqlite:///$PWD/demo.db" \
  python -m uvicorn app.main:app --port 8821
```

```bash
B=http://localhost:8821
ID=$(curl -s -F "file=@backend/tests/fixtures/01-northstar-line-sheet.xlsx" \
     $B/api/offers | python -c "import json,sys;print(json.load(sys.stdin)['offer_id'])")
curl -s $B/api/offers/$ID > before.json
```

Each attempt below is:

```bash
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"request_id":"'$(uuidgen)'","base_version":1,"changes":[ <CHANGE> ]}' \
  $B/api/offers/$ID/decisions
```

---

## The attempts, and the real responses

```
BEFORE: v1  1733 pieces  $4208.00  sha256=5333a88de0e5e7df

--- negative quantity
    {"line_id":"R9","action":"include","quantity":-5,"unit_cost":"9.00"}
    {"errors":[{"line_id":"R9","messages":["Pieces must be between 1 and 10,000,000; this is -5."]}]}
    HTTP 422

--- zero quantity
    {"line_id":"R9","action":"include","quantity":0,"unit_cost":"9.00"}
    {"errors":[{"line_id":"R9","messages":["Pieces must be between 1 and 10,000,000; this is 0."]}]}
    HTTP 422

--- fractional quantity
    {"line_id":"R9","action":"include","quantity":12.5,"unit_cost":"9.00"}
    {"errors":[{"line_id":"R9","messages":["Pieces must be a whole number. Send 45, not \"45\" or 45.0."]}]}
    HTTP 422

--- quantity as a string
    {"line_id":"R9","action":"include","quantity":"12","unit_cost":"9.00"}
    {"errors":[{"line_id":"R9","messages":["Pieces must be a whole number. Send 45, not \"45\" or 45.0."]}]}
    HTTP 422

--- price not a number
    {"line_id":"R9","action":"include","unit_cost":"abc"}
    {"errors":[{"line_id":"R9","messages":["Supplier cost 'abc' isn't an amount we can read. Use a decimal like \"4.50\"."]}]}
    HTTP 422

--- negative price
    {"line_id":"R9","action":"include","unit_cost":"-1"}
    {"errors":[{"line_id":"R9","messages":["Supplier cost must be above $0; this is -1.00."]}]}
    HTTP 422

--- zero price
    {"line_id":"R9","action":"include","unit_cost":"0"}
    {"errors":[{"line_id":"R9","messages":["Supplier cost must be above $0; this is 0.00."]}]}
    HTTP 422

--- price too precise
    {"line_id":"R9","action":"include","unit_cost":"1.23456"}
    {"errors":[{"line_id":"R9","messages":["Supplier cost '1.23456' isn't an amount we can read. 1.23456 has more than 4 decimal places."]}]}
    HTTP 422

--- price as a JSON number
    {"line_id":"R9","action":"include","unit_cost":9.0}
    {"errors":[{"line_id":"R9","messages":["Supplier cost must be a decimal string like \"4.50\"."]}]}
    HTTP 422

--- unknown line
    {"line_id":"R9999","action":"include"}
    {"errors":[{"line_id":"R9999","messages":["There is no line 'R9999' in this offer."]}]}
    HTTP 422

--- one bad in a batch of 3
    {"line_id":"R14","action":"include","unit_cost":"5.00"},
    {"line_id":"R9","action":"include","unit_cost":"abc"},
    {"line_id":"R12","action":"exclude"}
    {"errors":[{"line_id":"R9","messages":["Supplier cost 'abc' isn't an amount we can read. Use a decimal like \"4.50\"."]}]}
    HTTP 422

AFTER:  v1  1733 pieces  $4208.00  sha256=5333a88de0e5e7df

diff before/after:
    (identical — byte for byte)
```

Eleven rejections, and the offer never moved: same version, same pieces, same
total, **same sha256 of the entire GET response**.

The last case is the important one. Two of those three changes were perfectly
valid. Neither was applied — validation covers the whole batch and a single
failure rolls back everything, including the version bump. A partially
applied save would be worse than a rejected one.

## Why each is refused

**`"12"`, `12.0` and `true` are refused as quantities, not coerced.** They
are what a buggy client sends, not what a supplier typed, and accepting them
means guessing which the sender meant. Pydantic's default mode would happily
turn `"12"` into `12` and `true` into `1`, so `quantity` is a `StrictInt`
*and* `validate_change` checks the type again — deliberately twice, so the
rule holds even if a future endpoint forgets the annotation.

**Prices arrive as decimal strings.** A JSON float cannot represent `0.1`
exactly, so the wire format never carries one. `9.0` is refused for that
reason, while `"$4.50"` and `"1,200.50"` are accepted — the same parser the
spreadsheet goes through, because a person fixing a cost should be able to
type it the way the supplier wrote it.

**`1.23456` is refused rather than rounded.** More than four decimal places
is a data problem to surface, not something to silently change. Rounding
someone's price by a hundredth of a cent is still changing their price.

## Limits

Enforced on the *effective* values — the override if given, otherwise what
was parsed from the sheet:

| Field | Rule |
| --- | --- |
| item code | 1–64 characters after trimming, must be present |
| size | 1–64 characters after trimming, must be present |
| quantity | JSON integer, 1 to 10,000,000 |
| unit cost | decimal string, above $0, at most 4 decimal places, at most $1,000,000 |

Also refused: a save with no changes, the same `line_id` twice in one save,
a `request_id` reused with different changes, value overrides on an
`exclude` or `reset` (they would be stored without ever being checked), and
an upload over 10 MB.

## Tests

`test_invalid_values_are_refused_and_the_offer_is_unchanged` runs 17 bad
payloads through the API and compares the **entire offer JSON** before and
after each one. `test_one_bad_change_in_a_batch_applies_none` covers the
batch case, and `test_evaluate.py` covers the same rules at the pure-logic
layer, so they hold even for a caller that never touches HTTP.
