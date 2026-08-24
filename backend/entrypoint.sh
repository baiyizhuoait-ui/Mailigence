#!/bin/sh
# Mailigence backend container entrypoint.
#
# Responsibilities:
#   1. Ensure a CREDENTIAL_ENCRYPTION_KEY exists. It is only auto-generated on
#      the FIRST start and persisted to the mounted volume (/app/env), so
#      restarting the container never rotates the key — rotating it would make
#      every previously-encrypted mailbox credential undecryptable.
#   2. Run the app (python run.py), which auto-creates tables on startup.
set -e

ENV_DIR="${ENV_DIR:-/app/env}"
ENV_FILE="${ENV_DIR}/.env"

# Only touch the file when the key wasn't provided via the container
# environment (e.g. set explicitly in .env.docker).
if [ -z "${CREDENTIAL_ENCRYPTION_KEY:-}" ]; then
    mkdir -p "$ENV_DIR"
    touch "$ENV_FILE"

    if ! grep -q '^CREDENTIAL_ENCRYPTION_KEY=.\+' "$ENV_FILE"; then
        echo "CREDENTIAL_ENCRYPTION_KEY not set — generating and persisting one on first start." >&2
        KEY="$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")"
        printf 'CREDENTIAL_ENCRYPTION_KEY=%s\n' "$KEY" >> "$ENV_FILE"
    fi

    # shellcheck disable=SC1090
    . "$ENV_FILE"
    export CREDENTIAL_ENCRYPTION_KEY
fi

exec python run.py
