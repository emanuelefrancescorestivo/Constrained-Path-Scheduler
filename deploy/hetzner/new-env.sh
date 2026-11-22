#!/usr/bin/env bash
# Write the pilot's settings, deploy/hetzner/.env, for the first start (DECISIONS.md
# D181; docs/HETZNER.md, step 6). It asks three things (the domain, the address
# students write to, your name) and makes the two keys itself: the review page's and
# the one that seals students' links (D175). Everything else keeps env.example's value
# and can be changed later with: nano deploy/hetzner/.env
#
# It never overwrites a .env that exists: the keys in it may already be in use.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"
if [ -e .env ]; then
	echo "deploy/hetzner/.env exists already; change it with: nano deploy/hetzner/.env" >&2
	exit 1
fi

# A value as compose reads it from .env: in double quotes, with \ " and $ escaped, so
# a name such as D'Angelo or an address with a + arrives exactly as typed.
quoted() {
	local value=$1
	value=${value//\\/\\\\}
	value=${value//\"/\\\"}
	value=${value//\$/\$\$}
	printf '"%s"' "$value"
}

ask() { # ask <question> <variable>: again until something is typed
	local answer=""
	while [ -z "$answer" ]; do
		read -r -p "$1 " answer || { echo >&2; exit 1; }
		answer=$(printf '%s' "$answer" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
	done
	printf -v "$2" '%s' "$answer"
}

ask "The domain the app will answer on (for example plan.yourname.eu):" domain
domain=${domain#http://}
domain=${domain#https://}
domain=${domain%%/*}
domain=${domain,,}
if ! [[ $domain =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]]; then
	echo "\"$domain\" is not a domain name such as plan.yourname.eu" >&2
	exit 1
fi
ask "The e-mail address students can write to about their data:" contact
ask "Your name, as the one who answers for the data:" controller

admin=$(openssl rand -base64 24 | tr -d '/+=')
seal=$(openssl rand -base64 32 | tr '+/' '-_')

umask 077 # the file holds the keys: readable by you alone
while IFS= read -r line || [ -n "$line" ]; do
	case $line in
	CPS_DOMAIN=*) echo "CPS_DOMAIN=$domain" ;;
	CPS_CONTACT=*) echo "CPS_CONTACT=$(quoted "$contact")" ;;
	CPS_CONTROLLER=*) echo "CPS_CONTROLLER=$(quoted "$controller")" ;;
	CPS_ADMIN_TOKEN=*) echo "CPS_ADMIN_TOKEN=$admin" ;;
	CPS_SEAL_KEY=*) echo "CPS_SEAL_KEY=$seal" ;;
	*) printf '%s\n' "$line" ;;
	esac
done <env.example >.env.new
mv .env.new .env

cat <<EOF

Written: deploy/hetzner/.env (readable by you alone; Git ignores it).

Your review page will be https://$domain/admin/$admin
Keep that address, and this seal key, in your password manager:
  $seal

E-mail, AI and the rest are optional: nano deploy/hetzner/.env
Then start the app (docs/HETZNER.md, step 7):
  docker compose -f deploy/hetzner/compose.yaml up --detach --build --wait
EOF
