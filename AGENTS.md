# AGENTS.md

## Purpose

QuantBase uses Codex and other coding agents as delivery partners. Agents should keep changes small, update documentation when behavior changes, and verify work before calling it complete.

## Files To Read First

Before substantial work, read:

1. `README.md`
2. `docs/spec.md`
3. `docs/open-source-scope.md`
4. the directly relevant page or module documentation

## Operating Rules

1. Prefer small, reviewable changes over broad rewrites.
2. Keep QuantBase paper/simulation first. Do not enable real trading by default.
3. Never commit real API keys, exchange credentials, webhook URLs, server hosts, databases, logs, screenshots containing private account data, or local runtime files.
4. If a change touches strategy behavior, data sync, brokers, persistence, live execution, account state, or risk controls, add or update focused tests.
5. If product behavior, API contracts, page workflows, or risk boundaries change, update `docs/spec.md` and the matching page/module document.
6. Do not start long-running local frontend/backend services unless the user explicitly asks. Prefer `./scripts/check.sh`, targeted tests, compile checks, and static inspection.
7. Treat `data/seed/strategies.json` as public demo content only. Do not add private research results, production strategy parameters, or return claims.
8. Keep open-source docs neutral: no investment advice, no return promises, no copied production deployment instructions.

## Verification

Preferred non-service check:

```bash
./scripts/check.sh
```

Use narrower tests when they better match the change:

```bash
pytest -q tests/test_project_disclaimers.py
npm --prefix frontend run build
python3 -m compileall -q backend/app
```

## Done Criteria

A task is complete only when:

- the requested scope is implemented;
- relevant checks have run or skipped checks are explicitly documented;
- public-facing docs remain consistent with the paper/simulation-first boundary;
- no sensitive files or generated runtime artifacts are staged.
