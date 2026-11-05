# Repository agent instructions

## Personal email privacy

- Never expose the owner's personal email address, including any Proton Mail address, in public code, documentation, examples, issues, pull requests, logs, artifacts, or Git author/committer metadata. Never copy the actual address into policies, diagnostics, or reports.
- Use the verified GitHub noreply identity for `wintersyntax` for both Git author and committer. Verify it against the GitHub account identity and set both identities explicitly before every commit; do not rely on inherited local/global configuration.
- Before any push, pull request, release, deployment, or other publication, review the staged diff and all history reachable from the proposed published refs, including commit messages and author/committer metadata, for personal email exposure. Report findings without reproducing addresses.
- If exposure is found or identity/privacy verification is inconclusive, stop publication and report the blocker. Do not push, merge, rewrite history, or bypass repository gates to repair it without separate authorization.
