# Design: OpenGradient SDK Upgrade (v0.9.3)

## Objective
Upgrade the `opengradient` library from `0.9.0` to `0.9.3` to incorporate fixes and latest SDK features, ensuring the TEE-verified inference pipeline in `bot.py` remains fully functional.

## Changes

### 1. Dependencies
- Update `requirements.txt` to fix `opengradient==0.9.3`.
- Re-install the package in the local `venv`.

### 2. Architecture & Components
- **Initialization:** Verify `og.LLM(private_key=OG_PRIVATE_KEY)` still works.
- **Permit2 Approval:** Verify `ensure_opg_approval()` behavior (on-chain check).
- **Inference:** Test `await llm.chat()` to confirm model response and `payment_hash` structure.
- **Service Integration:** Restart `og-helper.service` to apply changes.

### 3. Verification & Testing (QA)
A temporary diagnostic script `verify_og_upgrade.py` will be used to validate the new SDK version against the current `.env` configuration.

- **Check 1:** Version verification (`pip show`).
- **Check 2:** SDK instantiation and configuration.
- **Check 3:** Mocked or actual LLM request (depending on token balance) to verify result object parsing.
- **Check 4:** Service logs monitoring via `bot.log`.

## Error Handling & Recovery
- **Fallback:** If initialization fails with `0.9.3`, rollback to `0.9.0` within `requirements.txt` and re-install.
- **Security:** Ensure `OG_PRIVATE_KEY` is not logged or exposed during verification.
- **Gateway Timeouts:** Handle potential network delays during TEE model loading.

## Documentation
- Update `.agent/MEMORY.md` with the new version and verification results.
