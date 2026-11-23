#!/bin/bash
# SSH set-up for Gadi in a GitHub Actions job (used by .github/workflows/mkfigs.yml).
# Reads GADI_KEY (and GADI_KEY_PASSPHRASE, if the key has one) from the environment, loads the
# key into an ssh-agent and exports the agent to later steps. Copied from
# ACCESS-NRI/COSIMA-recipes-workflow, via access-om3-live.
set -euo pipefail
mkdir -p ~/.ssh && chmod 700 ~/.ssh
printf '%s\n' "$GADI_KEY" > ~/.ssh/gadi_key && chmod 600 ~/.ssh/gadi_key
ssh-keyscan -H gadi.nci.org.au >> ~/.ssh/known_hosts
# Keep long, quiet steps (the Figshare upload) from being dropped.
printf 'Host gadi.nci.org.au\n  ServerAliveInterval 60\n  ServerAliveCountMax 10\n' >> ~/.ssh/config
eval "$(ssh-agent -s)"
if [[ -n "${GADI_KEY_PASSPHRASE:-}" ]]; then
    ASKPASS=$(mktemp) && chmod 700 "$ASKPASS"
    # shellcheck disable=SC2016  # expanded when ssh-add runs the script
    printf '#!/usr/bin/env bash\nprintf %%s "$GADI_KEY_PASSPHRASE"\n' > "$ASKPASS"
    DISPLAY=:0 SSH_ASKPASS="$ASKPASS" SSH_ASKPASS_REQUIRE=force ssh-add ~/.ssh/gadi_key </dev/null
    rm -f "$ASKPASS"
else
    ssh-add ~/.ssh/gadi_key
fi
rm -f ~/.ssh/gadi_key   # the agent holds the key now
echo "SSH_AUTH_SOCK=$SSH_AUTH_SOCK" >> "$GITHUB_ENV"
echo "SSH_AGENT_PID=$SSH_AGENT_PID" >> "$GITHUB_ENV"
