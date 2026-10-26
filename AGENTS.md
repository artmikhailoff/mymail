# Agent notes

## Production

Local app. No server deploy.

- Wait for `.github/workflows/test.yml` (`Test`) on the pushed sha.
- A green Test run means CI passed. It does not update a server. Say that in `DEPLOY.md`. Do not use conclusion `NO_CI` — this repo has a workflow.

## Tests

```text
python -m unittest discover -s tests
```

## Lint

none

## Typecheck

none

## Dependency audit

none

## Migrations

SQLite schema is created by the app. No separate migration command. Never commit `data/`, `.env`, or `credentials/*.env`.

## Post-deploy check

N/A. No server. After the Test workflow for this sha is green, write `PASS` with reason `local only`.

## Rollback

Disabled. Do not revert. Git history is the record; a person rolls back if they want to.
