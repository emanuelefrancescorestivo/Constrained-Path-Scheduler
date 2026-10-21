#!/usr/bin/env bash
# Put the newest `main` live on the pilot's server (DECISIONS.md D75). GitHub's
# Deploy workflow runs it over SSH after CI passes, with a key that may run this
# script and nothing else (docs/HETZNER.md, step 8); you can also run it by hand on
# the server: /srv/study-plan/deploy/hetzner/deploy.sh
#
# It builds the new image, starts it, and waits until the app answers /health; if
# it does not within three minutes, it fails, and so does the workflow (GitHub then
# e-mails you). The data volume is never touched.
#
# Everything is inside main(), called on the last line, so bash has read the whole
# file before `git reset` replaces it with the new version.
set -euo pipefail

main() {
	cd "$(dirname "$(readlink -f "$0")")/../.."
	git fetch --quiet origin main
	git reset --quiet --hard origin/main
	docker compose -f deploy/hetzner/compose.yaml up --detach --build --remove-orphans --wait --wait-timeout 180
	docker image prune --force >/dev/null
	echo "study-plan: $(git log -1 --format='%h %s') is live"
}

main "$@"
