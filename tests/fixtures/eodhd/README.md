# EODHD response fixtures

**Captured from the live API on 2026-09-01 using EODHD's own public `demo`
token**, not written by hand. No key is present in any file and none is needed
to re-capture them:

    curl "https://eodhd.com/api/eod/AAPL.US?api_token=demo&fmt=json&from=2020-08-27&to=2020-09-02"
    curl "https://eodhd.com/api/splits/AAPL.US?api_token=demo&fmt=json&from=2020-01-01&to=2020-12-31"
    curl "https://eodhd.com/api/div/AAPL.US?api_token=demo&fmt=json&from=2020-01-01&to=2020-06-30"

This matters. `acquisition/eodhd.py` was deliberately left unfinished with the
reason stated: guessing URL shapes and then writing tests against fixtures built
from the same guesses "would produce a green suite that proves nothing and an
adapter that fails on first contact with the real API". These fixtures are the
answer to that — the field names in them are the vendor's, verified.

AAPL's August 2020 4-for-1 split is chosen deliberately: it is the window where
`close` and `adjusted_close` diverge sharply, so a test that confused them
cannot pass quietly.
