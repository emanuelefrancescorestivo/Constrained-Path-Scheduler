# The pilot on a server of its own (Hetzner)

*Checkpoint C1 of `docs/ROADMAP.md`, the way the owner chose on 2026-10-19
(DECISIONS.md D75). The owner does these steps: they open an account, pay and buy a
domain, which this repository never does on anyone's behalf. Each step says what it
does and why, since learning how an app really runs is half the point. Prices and
the look of Hetzner's pages come from third-party write-ups and could not be checked
from here: they are marked **(check)**.*

Your laptop runs Windows: the commands marked **PowerShell** are typed there; the
others are typed on the server, after `ssh` has signed you in.

## What you will have

```
 a student's phone ──HTTPS──▶ Caddy ──HTTP, inside the server──▶ the app ──▶ cps.sqlite
                              (ports 80 and 443)                 (port 8000,     (the `data`
                              the certificate                     not reachable   volume, with
                              from Let's Encrypt                  from outside)   7 daily copies)
```

- **A server** at Hetzner in Germany, running Ubuntu 24.04. Only three doors are
  open: SSH (for you, with your key, never a password), HTTP and HTTPS.
- **Two containers**, described by `deploy/hetzner/compose.yaml`:
  - *the app*: the image made by the `Dockerfile` at the root, the same code as
    the demo;
  - *Caddy*: it gets an HTTPS certificate for your domain from Let's Encrypt, renews
    it by itself, and passes every request to the app.
- **The data** in a Docker volume, which survives every update. The app copies its
  database there once a day and keeps a week; Hetzner keeps a week of copies of the
  whole server, elsewhere.
- **Updates**: Ubuntu installs security fixes every night (and restarts at 04:30
  when one needs it). The app updates itself when you merge into `main` and CI
  passes (step 8).

**What it costs (check).** Third-party summaries in 2026 give, per month: the
smallest server (2 vCPU, 4 GB) €4 to €6, a public IPv4 address about €0.50, the
backups 20 % of the server's price. So about €6 to €8 a month, plus the domain, €5
to €15 a year depending on its ending. Hetzner bills by the hour, up to the monthly
price, and deleting the server stops the bill.

**Time:** an afternoon, most of it waiting for DNS.

## 1. A key for SSH (PowerShell)

SSH is how you will type commands on the server. Instead of a password it uses a
pair of keys: a private one that never leaves your laptop, and a public one you give
to the server. Whoever holds the private key can sign in; the public one is safe to
share.

```powershell
ssh-keygen -t ed25519 -C "study-plan"
```

Press Enter to keep the place it suggests (`C:\Users\<you>\.ssh\id_ed25519`), and
choose a passphrase: it protects the key if the laptop is lost. Then show the public
half, one line starting with `ssh-ed25519`:

```powershell
Get-Content $HOME\.ssh\id_ed25519.pub
```

## 2. A Hetzner account

At [hetzner.com](https://www.hetzner.com/cloud/), sign up, confirm your e-mail and
add a payment method. Hetzner may ask for an identity check before the first server
(check). In the **Cloud Console**, create a project, for example `study-plan`.

## 3. The server

In the project: **Add Server** (check the names on the page).

| | Choose |
|---|---|
| Location | **Nuremberg** or **Falkenstein**: Germany, so the data stays with a German company in the EU |
| Image | **Ubuntu 24.04** |
| Type | the smallest **shared vCPU** one: **CX23** (Intel/AMD) or **CAX11** (Arm), 2 vCPU and 4 GB. Either works: the image is built on the server for its own processor. If both are sold out in one location (reported in October 2026, check), try the other location, or the next size (CPX22) |
| Networking | **Public IPv4** and **IPv6**: some student networks have no IPv6 yet |
| SSH keys | **Add SSH key**: paste the line from step 1. Without it, Hetzner e-mails a root password, which this setup never uses |
| Backups | **On**: a copy of the whole server each day, seven kept, stored apart from it |
| Cloud config | paste the whole of `deploy/hetzner/cloud-init.yaml`, **after** replacing `ssh-ed25519 PASTE-YOUR-PUBLIC-KEY-HERE` with the same line from step 1 |
| Name | `study-plan` |

Optional, and free: a **Cloud Firewall** (Firewalls, Create Firewall) allowing in
only TCP 22, 80 and 443 and UDP 443, applied to the server. The server has its own
firewall already (`ufw`); this one stops the rest before it even reaches it.

**Create & Buy now.** Write down the server's IPv4 and IPv6 addresses.

*What the cloud config does*, in the minutes after the server starts: installs
Docker, Git, a firewall and automatic updates; makes a user `deploy` with your key;
turns off passwords and root's sign-in; opens SSH, HTTP and HTTPS only; and fetches
this repository into `/srv/study-plan`. If the key line was left as it was, it keeps
root's sign-in so that you are not locked out, and says so when you sign in.

## 4. A domain

Passkeys (D35) and the installable app (D33) need HTTPS on a real name; a bare IP
address will not do. It does not have to be the product's name: a domain of your
own, such as `yourname.eu`, with the app at `plan.yourname.eu`, works and leaves the
name for later. **Choose it once**: passkeys belong to the name, so moving to
another one later means students add theirs again.

Buy it from any registrar; Hetzner sells domains too (check prices: a `.eu` was
reported at about €10 a year, a `.com` at about €13.50). In the registrar's **DNS**
settings, add two records for the name the app will use (here `plan`):

| Type | Name | Value |
|---|---|---|
| A | `plan` | the server's IPv4 address |
| AAAA | `plan` | the server's IPv6 address (the one ending in `::1` within the block Hetzner shows) |

Check that it has spread (PowerShell); both lines must show the server's addresses
before step 7:

```powershell
Resolve-DnsName plan.yourname.eu
```

## 5. Signing in (PowerShell)

```powershell
ssh deploy@<the server's IPv4 address>
```

The first time, SSH shows the server's fingerprint and asks whether to trust it:
type `yes`. Then, on the server:

```bash
cloud-init status --wait      # "status: done" once the cloud config has finished
sudo ufw status               # 22, 80 and 443 allowed, nothing else
ls /srv/study-plan            # the repository
```

If `deploy` is refused, or the welcome text says *no key for deploy*, the key line of
the cloud config was left as it was. The simplest fix is to delete the server and
create it again with the line filled in (step 3; it costs a few cents). Root still
signs in with the key chosen in the console meanwhile (`ssh root@<address>`), and
the rules that shut out root and passwords were not applied.

## 6. The settings

```bash
cd /srv/study-plan
openssl rand -base64 24 | tr -d '/+='    # a key for your review page: copy it
nano deploy/hetzner/.env
```

Fill in `CPS_DOMAIN` (for example `plan.yourname.eu`), `CPS_CONTACT`,
`CPS_CONTROLLER` (your name) and `CPS_ADMIN_TOKEN` (the line just made). Leave
`ANTHROPIC_API_KEY` empty unless you want AI (D12). Save with Ctrl+O, Enter, then
Ctrl+X. This file stays on the server: Git ignores it, and it should never be pasted
into a chat or an issue.

**E-mail (D94).** Students sign up with their e-mail and a password. The app sends
e-mail for two things only: the link that confirms an address and the link that
chooses a new password. For that it needs an SMTP service: make an account with one
(a transactional e-mail service, or your university's mail if it allows it), let it
check your domain if it asks (it gives DNS records to add, as in step 4), and fill
in `CPS_SMTP_HOST`, `CPS_SMTP_PORT`, `CPS_SMTP_USER`, `CPS_SMTP_PASSWORD`,
`CPS_MAIL_FROM` and `CPS_MAIL_PROVIDER` (its company and country, for the privacy
page). Without them the pilot still works: students can confirm later, and a student
who forgets a password gets a link you make on
`https://plan.yourname.eu/admin/<CPS_ADMIN_TOKEN>/inbox` and send yourself.

## 7. The first start

```bash
docker compose -f deploy/hetzner/compose.yaml up --detach --build --wait
```

The first build takes a few minutes. Then:

- open `https://plan.yourname.eu`: the padlock is Caddy's certificate;
- the privacy page names you and Hetzner;
- `https://plan.yourname.eu/admin/<CPS_ADMIN_TOKEN>` is your review page;
- `docker compose -f deploy/hetzner/compose.yaml ps` shows `app` as *healthy*.

If the page does not load, `docker compose -f deploy/hetzner/compose.yaml logs
caddy` says why; most often the DNS of step 4 has not reached everyone yet. Caddy
tries again by itself.

Then go through setup on your phone with your own timetable, as C1 asks.

## 8. Updates from GitHub, by themselves

After this step, every merge into `main` goes live once CI has passed, by the
**Deploy** workflow (`.github/workflows/deploy.yml`). GitHub signs in to the server
with a key of its own, which may run `deploy/hetzner/deploy.sh` and nothing else:
fetch `main`, build, start, wait until the app answers, fail loudly if it does not.

**A key for GitHub** (PowerShell). Press Enter at both passphrase questions: GitHub
cannot type one.

```powershell
ssh-keygen -t ed25519 -C "github-deploy" -f $HOME\.ssh\study-plan-deploy
Get-Content $HOME\.ssh\study-plan-deploy.pub
```

**On the server**, allow that key to deploy and nothing more (paste the public line
where it says so, keeping the quotes):

```bash
echo 'command="/srv/study-plan/deploy/hetzner/deploy.sh",restrict PASTE-THE-PUBLIC-LINE' >> ~/.ssh/authorized_keys
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub   # the server's fingerprint, for the next check
```

**The server's own key**, so that GitHub talks to your server and no other
(PowerShell; its fingerprint must match the line the server just printed):

```powershell
ssh-keyscan -t ed25519 plan.yourname.eu
ssh-keyscan -t ed25519 plan.yourname.eu | ssh-keygen -lf -
```

**In GitHub**, the repository's **Settings > Secrets and variables > Actions**:

| Where | Name | Value |
|---|---|---|
| Variables | `DEPLOY_HOST` | `plan.yourname.eu` |
| Secrets | `DEPLOY_SSH_KEY` | the whole of `study-plan-deploy` (the file **without** `.pub`), from `-----BEGIN` to `-----END ...-----` |
| Secrets | `DEPLOY_KNOWN_HOSTS` | the line `ssh-keyscan` printed first |

GitHub keeps secrets encrypted and never shows them again, even to you. Try it:
**Actions > Deploy > Run workflow**. The run ends with *... is live*.

Until `DEPLOY_HOST` exists, the workflow skips itself, so nothing fails before the
server is ready.

## Backups, and getting data back

Two kinds, both on from the start:

1. **The app's own copies**: at start and once a day, in the `data` volume
   (`/data/backups`), seven days kept. They help after a bad release or a damaged
   file, not after losing the server.
2. **Hetzner's backups** (step 3): the whole server once a day, seven kept, stored
   apart from it. They help after losing the server, or a mistake in its settings;
   a backup comes back whole, as a new server or over this one.

To put the database back from one of the app's copies:

```bash
cd /srv/study-plan
C="docker compose -f deploy/hetzner/compose.yaml"
$C exec app ls /data/backups                     # pick one, e.g. cps-20261020-031500.sqlite
$C stop app
$C run --rm --no-deps --entrypoint sh app -c \
  'cp /data/backups/cps-20261020-031500.sqlite /data/cps.sqlite && rm -f /data/cps.sqlite-wal /data/cps.sqlite-shm'
$C start app
```

A copy away from Hetzner altogether (a Storage Box, or another provider) is the next
step once real students depend on it; it is not set up here.

## Looking after it

- Once a week: `docker compose -f deploy/hetzner/compose.yaml ps` (healthy?),
  `df -h /` (space left?), and a look at the review page.
- Security updates install themselves; `cat /var/log/unattended-upgrades/unattended-upgrades.log` says which.
- Optional: a free uptime monitor that visits `https://plan.yourname.eu/health` every
  few minutes and e-mails you if it stops answering (several services offer one;
  it is another account, so it is your choice).
- Locked out, or the server will not start: the Cloud Console has a **Console**
  (a screen and keyboard in the browser) and a **Rescue** mode.

## What changes for the demo

Nothing: the demo (`deploy/demo/render.yaml`) stays on Render's free plan, made-up
students only. `render.yaml` at the root stays as a second way to run the pilot; it
is not used while the pilot runs here.
