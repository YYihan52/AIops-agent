#!/usr/bin/env bash
# Seed a PUBLIC GitHub repo for the code-fix path (replaces the old local-Gitea flow).
#
# What it does (run by the maintainer; re-run any time to reset the upstream repo):
#   1. ensure a PUBLIC upstream repo  {OWNER}/{REPO}  (default branch = master)
#   2. build the local git history (scripts/seed_github_history.py):
#        commit 1    clean baseline — bounded recent-id cache, no features
#        commits 2-7 ONE feature-with-bug per commit (natural dev-history style):
#                    ranking / dedupe / stats / pagination / cache / pricing
#        commit 8    the leak — unbounded seen-id history  <- the suspect deploy
#        commit 9    docs: BUGS.md known-issues list (+ README pointer)
#      EVERY planted bug lives on master (Phase B layout): a plain clone shows
#      all of them; scripts/seed_github_history.py self-checks each stage
#      (py_compile + pytest: the failing set must be exactly the bugs so far).
#   3. force-push that history to the upstream repo's master
#   4. restore master branch protection (PRs only; nobody pushes master directly)
#   5. write deploys.log (image tag <-> git SHA <-> time) — the deploy-history trail
#   6. refresh workspace/{REPO} to the new master (the diagnose Agent reads code there)
#
# Real-OSS demonstration model:
#   - This repo is PUBLIC. Each user FORKS it to their own account, then sets
#     GITHUB_FORK_OWNER=<their-username> + GITHUB_TOKEN=<their-PAT>. The code-fix Agent
#     clones THEIR fork, pushes a bugfix branch there, and opens a CROSS-FORK PR whose
#     base is THIS upstream repo. No collaborator invite needed; all PRs land here.
#   - The maintainer can also run the Agent directly (GITHUB_FORK_OWNER == OWNER) -> same-repo PR.
#   - NOTE: force-pushing resets master history. users who forked BEFORE a re-seed
#     must delete + re-fork (or hard-sync their fork's master) — old forks keep the
#     retired history.
#
# Requires: `gh` (authenticated: gh auth status), `git`, and a python3 with pytest
# available (the repo's .venv is used if present).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE="$ROOT/scripts/fixtures/recommendation-master"

OWNER="${GITHUB_UPSTREAM_OWNER:-HuaiNan54321}"
REPO="${GITHUB_REPO:-recommendation}"
DEFAULT_BRANCH="${GITHUB_DEFAULT_BRANCH:-master}"
GIT_EMAIL="${GITHUB_SEED_EMAIL:-aiops@example.com}"
GIT_NAME="${GITHUB_SEED_NAME:-aiops}"
DESCRIPTION="AIOps example — a legacy recommendation service with known open issues (see BUGS.md). Fork me and let the Agent open a PR."

command -v gh >/dev/null || { echo "[seed] gh CLI not found. Install: https://cli.github.com/"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "[seed] gh not authenticated. Run: gh auth login"; exit 1; }

# python for the history builder + its pytest self-checks (repo venv preferred)
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

FULL="$OWNER/$REPO"

echo "[seed] ensuring PUBLIC repo $FULL exists…"
if gh repo view "$FULL" >/dev/null 2>&1; then
  echo "[seed] repo exists; ensuring it is public + description is current…"
  gh repo edit "$FULL" --visibility public --accept-visibility-change-consequences >/dev/null 2>&1 || true
  gh repo edit "$FULL" --description "$DESCRIPTION" >/dev/null 2>&1 || true
else
  gh repo create "$FULL" --public --description "$DESCRIPTION" >/dev/null
  echo "[seed] created $FULL (public)."
fi

# --- build the full master history locally (self-checked), then push it ---
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "[seed] building the multi-bug master history (self-check: py_compile + pytest per commit)…"
"$PY" "$ROOT/scripts/seed_github_history.py" \
  --fixture-dir "$FIXTURE" \
  --work-dir "$WORK/repo" \
  --default-branch "$DEFAULT_BRANCH" \
  --service "$REPO" \
  --git-name "$GIT_NAME" --git-email "$GIT_EMAIL" \
  --out "$WORK/manifest.json" \
  --deploys-log "$ROOT/deploys.log"

# Temporarily lift branch protection so the force-push (reset of history) can land.
gh api -X DELETE "repos/$FULL/branches/$DEFAULT_BRANCH/protection" >/dev/null 2>&1 || true
git -C "$WORK/repo" remote add origin "https://github.com/$FULL.git"
# gh provides the credential helper, so no token needs to appear on the command line.
git -C "$WORK/repo" -c credential.helper='!gh auth git-credential' push -q --force -u origin "$DEFAULT_BRANCH"

# --- master branch protection: PRs only ---
# enforce_admins=true is ESSENTIAL: without it the repo owner (you) can still push master
# directly, which breaks the demo's "推不上 master，只能提 PR" guarantee. required_pull_request_reviews
# (count 0) is what makes GitHub reject direct pushes with "Changes must be made through a pull request".
echo "[seed] enabling '$DEFAULT_BRANCH' branch protection (PRs only, enforced for admins too)…"
gh api -X PUT "repos/$FULL/branches/$DEFAULT_BRANCH/protection" \
  -H "Accept: application/vnd.github+json" \
  --input - >/dev/null 2>&1 <<'JSON' || \
  echo "[seed] (branch protection API returned non-2xx; re-run or set it in repo Settings > Branches)"
{
  "required_status_checks": null,
  "enforce_admins": true,
  "required_pull_request_reviews": { "required_approving_review_count": 0 },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON

# --- refresh the diagnose-time workspace clone (the diagnose Agent reads code here) ---
if rm -rf "$ROOT/workspace/$REPO" && git clone -q --depth 50 "https://github.com/$FULL.git" "$ROOT/workspace/$REPO"; then
  echo "[seed] workspace/$REPO refreshed to the new master."
else
  echo "[seed] WARN: could not refresh workspace/$REPO (diagnose Agent will read a stale clone)."
fi

# --- summary from the manifest ---
echo ""
echo "[seed] DONE. master layout (all bugs live on master, one commit each):"
"$PY" - "$WORK/manifest.json" <<'PY'
import json, sys
for e in json.load(open(sys.argv[1])):
    note = f"  -> {e['deploy_note']}" if e["deploy_note"] else ""
    print(f"  {e['sha']}  {e['subject']}{note}")
PY
echo ""
echo "  upstream repo:  https://github.com/$FULL"
echo "  bug list (in-repo, user-facing): BUGS.md"
echo "  deploys.log written to repo root (LATEST_DEPLOY = the leak commit)"
echo ""
echo "  maintainer (same-repo PR):"
echo "    export GITHUB_TOKEN=\$(gh auth token)"
echo "    export GITHUB_UPSTREAM_OWNER=$OWNER GITHUB_REPO=$REPO"
echo "    # GITHUB_FORK_OWNER defaults to UPSTREAM_OWNER -> same-repo PR"
echo ""
echo "  user (fork + cross-fork PR — no invite needed):"
echo "    1) Fork https://github.com/$FULL to your account (AFTER this seed — forks keep old history)"
echo "    2) Create a PAT (scope: public_repo) at https://github.com/settings/tokens"
echo "    3) export GITHUB_TOKEN=<your-PAT>"
echo "       export GITHUB_UPSTREAM_OWNER=$OWNER GITHUB_REPO=$REPO"
echo "       export GITHUB_FORK_OWNER=<your-github-username>"
