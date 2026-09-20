# SDD ledger — plan: docs/superpowers/plans/2026-09-19-remote-obsidian-wechat-deepseek.md

Pre-flight: shared interfaces

- Task 1 → Task 5: Task 1 owns `AIProcessor.analyze(item) -> dict` coverage, evidence, provider, model, and completion metadata; Task 5 consumes those exact fields. Ruling: keep the existing `ContentItem` caller contract and add metadata fields, because the spec requires backwards-compatible pipeline writes; cost if wrong: downstream note/database compatibility work.
- Task 1 → Task 6: Task 1 owns `KNOWLEDGE_OPENAI_BASE_URL`, `OPENAI_MODEL`, `OPENAI_API_KEY`, and `AI_ENABLED`; Task 6 supplies them only in Ubuntu local configuration. Ruling: keep secrets out of deployment artifacts and Git; cost if wrong: production cannot be enabled until local configuration is corrected.
- Task 2 → Task 3: Task 2 adds `syncthing@knowledge-manager.service` as a backup companion; Task 3 must install that exact unit and preserve its service name. Ruling: use the same systemd instance name in backup and installer; cost if wrong: backup either races sync writes or aborts before archiving.
- Task 2 → Task 6: Task 6 depends on backup retention and service quiescence before remote restore evidence. Ruling: no production acceptance claim until both Ubuntu and D: restore checks pass; cost if wrong: incomplete recovery evidence.
- Task 3 → Task 6: Task 3's check command reports Tailscale, Syncthing, Vault, versioning, and conflict state; Task 6 records those results for Windows/Android pairing. Ruling: a connected device without file-hash convergence is not acceptance; cost if wrong: silent client divergence.
- Task 4 → Task 6: Task 4 owns the single `/wechat/callback` public route and tunnel unit; Task 6 supplies real hostname and credentials. Ruling: keep Cloudflare and WeChat disabled until all local credentials validate; cost if wrong: exposing an unconfigured public entry.

Ruling: the implementation source is `vendor/knowledge-inbox/backend`; the repository's tests and deployment scripts already import and package that vendored application, so a parallel backend copy would create divergent runtime code.

Ruling: the former local Qwen service remains disabled and is not removed in this pass; DeepSeek is the only planned analysis provider, while the retained local model files remain outside Git and production data.

Todo:

- [x] Task 1: DeepSeek provider and grounded AI aggregation (focused tests and provider-neutral checker pass; full repository collection remains blocked by ignored runtime llama test dependencies)
- [x] Task 2: Backup quiescence and retention (focused backup/retention tests pass; Linux systemd fixture awaits Ubuntu host)
- [x] Task 3: Tailscale/Syncthing Vault synchronization (configuration tests pass; real device pairing and Ubuntu check remain deployment gates)
- [x] Task 4: Cloudflare Tunnel and WeChat public callback (offline protocol/static entry tests pass; real Cloudflare and WeChat credentials remain deployment gates)
- [x] Task 5: Live DeepSeek acceptance checker (provider-neutral redacted checker and invariant tests pass; live key/config gate remains)
- [ ] Task 6: Ubuntu deployment and end-to-end acceptance (service graph and fail-closed activation tests pass; real Ubuntu, DeepSeek, Cloudflare, WeChat, Windows and Android evidence still pending)
- [ ] Task 7: Whole-branch review and GitHub delivery
