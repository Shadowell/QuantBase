# Open Source Release Checklist

Use this checklist before publishing or tagging a public QuantBase release.

## Scope

- [ ] README and `docs/open-source-scope.md` describe the community edition boundary.
- [ ] `docs/spec.md` still states that QuantBase is paper/simulation first.
- [ ] `docs/local-deployment.md` matches the current `init.sh`, `start.sh`, `status.sh`, `stop.sh`, and `scripts/check.sh` behavior.
- [ ] Demo seeds are public examples only and do not include private research parameters or return claims.

## Sensitive Data

- [ ] No API keys, exchange credentials, webhook URLs, production hosts, private databases, logs, screenshots, or local runtime files are committed.
- [ ] `.env.example` contains placeholders and safe defaults only.
- [ ] Generated reports, caches, and local data stores are ignored or absent.

## Risk Language

- [ ] No docs, examples, UI copy, seeds, or tests present the project as investment advice.
- [ ] No docs, examples, UI copy, seeds, or tests promise returns, low risk, managed accounts, or copy-trading outcomes.
- [ ] Real-account and MCP mutation paths require explicit opt-in flags.

## Verification

- [ ] `./scripts/check.sh`
- [ ] `./init.sh` on a clean checkout or documented equivalent setup validation.
- [ ] Temporary local smoke test with `./start.sh`, health curls, `./status.sh --json`, and `./stop.sh`.
- [ ] Targeted tests for changed behavior.
- [ ] Frontend build if UI changed.
