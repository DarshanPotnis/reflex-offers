# Reflex supplier offer tool

Work sample for Reflex Sales Group. Upload a supplier line sheet, review what it
means, resolve questionable rows, save an offer at a shareable link, and export
a clean CSV.

Status: parsing engine complete and tested. See `CLAUDE.md` for the full
design and `PROMPTS.md` for the remaining build phases.

## Run the tests

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```
