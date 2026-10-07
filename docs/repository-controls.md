# Repository controls

The repository settings script never changes visibility. Preview its changes:

```sh
python scripts/configure-repository.py --repo dylan-murray/iap-portal
```

Append `--apply` to enable the controls available while private: restricted Actions,
full-SHA pinning, read-only workflow tokens, Dependabot alerts and security fixes,
and deletion of merged branches. The authenticated account needs admin access.

## Public launch

GitHub's current plan does not support private-repository rulesets or protected
release environments. CodeQL and dependency review are gated until public.
After the owner approves and changes visibility, immediately run:

```sh
python scripts/configure-repository.py --repo dylan-murray/iap-portal --public-features --apply
```

This requires an already-public repository. It installs the versioned rules in
`.github/repository-rules/`, enables secret scanning and push protection, private
vulnerability reports, and approval for all external contributors' workflow runs.
The `release` environment requires the owner's approval and protected branches.

Main requires a pull request, one approving review, code-owner review, resolved
threads, and current passing CI, Semgrep, CodeQL, and dependency-review checks.
Admin bypass is limited to pull requests so the sole owner can merge their own
reviewed changes. Branches reject deletion and force pushes; Dependabot branches
are exempt from that general rule. Version tags can only be managed by admins.

## Verify before publishing a release

- Inspect Settings → Rules → Rulesets: all three rulesets must be active.
- Inspect Actions settings: selected Actions only, full SHA pinning, read-only
  tokens, no workflow-created approvals, all external contributors need approval.
- Inspect Code security: alerts, security updates, secret scanning, push protection,
  and private reporting must be enabled.
- Inspect the release environment: owner review and protected branches only.
- Run CI, security, and CodeQL on main after launch. Open a small pull request to
  confirm every required check appears, including dependency review.
- Run release publishing from main only, after checks pass. The release tag must
  point to a commit in main. Publishing stays disabled while the repo is private.

The script is repeatable and updates rulesets by name. A failed API request stops
the script; earlier settings may already have applied. Fix the cause, rerun, and
verify the settings above. Never treat a partially completed run as protected.
