# Deploying the pilot

*Checkpoint C1 of `docs/ROADMAP.md`. The owner does these steps: they create an
account, accept terms and start paying, which this repository never does on anyone's
behalf. Render's documentation was read again on 2026-10-19 (Blueprints, the
Blueprint specification, disks, compute plans, SSH and the Shell); what it confirmed
is stated plainly below, and what could not be checked from here, such as the exact
look of a dashboard page or a price, is marked **(check)**.*

## What gets deployed

`render.yaml` describes one web service:

| | |
|---|---|
| region | Frankfurt, so the data stays in the EU |
| plan | `0.5c-512mb`: 0.5 CPU and 512 MB, the smallest paid plan ("Starter" before Render renamed its plans on 2026-08-26; the price did not change). A disk needs a paid plan |
| disk | 1 GB at `/var/data`: the store `cps.sqlite` and a week of daily backups |
| start | `cps web --host 0.0.0.0 --behind-proxy --db /var/data/cps.sqlite` |
| health check | `/health` |
| deploys | by hand (`autoDeploy: false`), after CI is green on `main` |

**What it costs (check).** Third-party summaries of Render's prices in 2026 give $7 a
month for this plan and $0.25 per GB of disk a month, so about $7.25 a month with
the 1 GB disk; Render's own pricing page did not show its figures to the tool that
read it. Look at render.com/pricing before confirming. The demo stays free.

Reminders (D38) are sent by the running app, every minute: the instance must stay
awake. A paid instance does; a free one that sleeps when idle sends them only while
someone is using it.

The app deletes plans 12 months after they were last opened (DECISIONS.md D36;
checked every six hours) and copies the store to `/var/data/backups` at start and
once a day, keeping seven days, which is what the privacy page promises. A copy on
the same disk protects against a bad release or a corrupted file, not against losing
the disk. Render also snapshots the disk once every 24 hours and keeps each snapshot
for at least seven days; a snapshot is restored whole, from the service's Disk page.

A service with a disk runs as one instance ("You can't scale a service to multiple
instances if it has a disk attached") and stops before the new version starts, so
each deploy interrupts it for a moment. For a pilot of 10 to 20 students, one
instance is ample; the rate limits are in memory for that reason. The free demo,
with its made-up classmates, runs in the same 512 MB.

## Steps

1. **Account and payment.** Sign in at render.com with the GitHub account that owns
   the repository (the one the demo already runs under). Add a payment method
   **(check where: the workspace's billing settings)**.
2. **Blueprint.** In the dashboard, **New**, then **Blueprint**. **Connect** the
   repository, name the Blueprint (for example `study-plan`), choose the `main`
   branch, and leave **Blueprint Path** empty: the pilot's file is the
   `render.yaml` at the root (the demo's is `deploy/demo/render.yaml`). Render lists
   what it will make: one web service, `study-plan`, with a disk.
3. **Settings.** Render asks for the values marked `sync: false`, and only this
   once: later, change them on the service's Environment page **(check the name)**,
   which restarts it.
   - `CPS_CONTACT`: the e-mail address students can write to about their data. It
     appears on the privacy page.
   - `CPS_CONTROLLER`: your name, as the person who runs the service and answers for
     its data (the controller, under the GDPR, D6). It appears on the privacy page
     with the host, which `render.yaml` already names: "Render (Frankfurt, EU)"
     (D72).
   - `CPS_ADMIN_TOKEN`: a long random string (24 characters or more), made on your
     own computer, for example with
     `python -c "import secrets; print(secrets.token_urlsafe(32))"`. The page where you
     review what students report is `/admin/<that string>`; keep it as private as a
     password and never paste it in a chat. Left empty, there is no review page, and
     reported posts stay hidden after three reports until you set it (DECISIONS.md
     D20). Students' messages from "Send feedback" and the error log are at
     `/admin/<that string>/inbox` (D43): read it with the reports, once a day during
     the pilot.
   - `CPS_BASE_URL`: leave empty, unless a custom domain is added later. The app then
     uses the address Render gives it (`RENDER_EXTERNAL_URL`).
     Passkeys (D35) belong to that address: after a move to another domain,
     students open their plan with its address or recovery code, and add a passkey
     again.
   - `ANTHROPIC_API_KEY`: leave empty to start. Without it there is no AI: tasks
     typed in words are read by rules, the weekly review uses its template and the
     coach answers from the tips (DECISIONS.md D12, D65). To turn it on later, see
     "AI, later" below.
   - `CPS_VAPID_PRIVATE_KEY` is not asked for: the app makes the key that signs
     reminders (D38) on first use and keeps it in its database on the disk.
4. **Deploy.** **Deploy Blueprint**. The build installs the package with the `web`
   and `ai` extras; the log should end with `serving on http://0.0.0.0:<port>` and
   the health check turning green. If the build says Python 3.13 is not available in
   that form, set `PYTHON_VERSION` to a full version the error message lists (for
   example `3.13.7`) **(check)**.
5. **Read the privacy page** at `https://study-plan-….onrender.com/privacy`, in
   English, French and Italian (the language is in Settings, or `?lang=fr`). It
   should name you, your contact address and Render in Frankfurt. This is C2's first
   half: approving it is yours.
6. **Try it on a phone.** Open the service's address, paste your own timetable link,
   check the exams, add a deadline, save. Then:
   - add the feed to Google Calendar on a computer (Other calendars, +, From URL) and
     check that the sessions appear, possibly hours later;
   - open one session's event in the calendar, follow its link, tap Done, and see
     Today change;
   - add the app to the home screen, turn reminders on, and add a passkey;
   - open `/admin/<your string>/inbox` and send yourself a message with "Send
     feedback", to see where students' messages arrive.
7. **Report back** what differed from these steps, so this page can be corrected.

## Operating it

- **Logs** show method, route, status and time, never a plan's address.
- **A release**: merge to `main`, wait for CI, then Manual Deploy in the dashboard
  **(check)**. Each deploy interrupts the service for a moment (one instance with a
  disk): release in the evening, not before an exam.
- **Read the pilot's numbers** once a week, from the service's **Shell** (paid
  services have one, and SSH): `cps metrics --db /var/data/cps.sqlite` (or `--json`).
  Active students, sessions confirmed, return on the 7th and 30th day, streaks, the
  network, the month's AI spending (DECISIONS.md D15).
- **Restore** a backup: either the app's own copy (stop the service, copy
  `/var/data/backups/cps-<date>.sqlite` over `/var/data/cps.sqlite` from the Shell,
  start it again), or Render's snapshot of the whole disk, from the service's Disk
  page (everything after the snapshot is lost).
- **Delete everything**: delete the service and its disk in the dashboard.

## AI, later

With `ANTHROPIC_API_KEY` set (your key from Anthropic's console), each student still
chooses in Settings. `CPS_AI_MONTHLY_CAP_USD` (10 in `render.yaml`) stops calls for
the rest of the month once the month's spending would pass it; `CPS_AI_MODEL`
(default `claude-opus-5-5`; `claude-haiku-5-5` costs a fortieth as much per token)
picks the model. Set a spending limit in Anthropic's console as well: the cap here
counts what this server asked for, not what Anthropic billed. The coach and the
typed tasks have only answered a fake model so far (CLAUDE.md, known limits): read
their first real answers before telling students about them.

## Before other students use it (C2)

- The owner reads and approves `/privacy` (step 5) and `/terms`.
- Two or three friends go through step 6 with their own timetables; what confused
  them goes into the roadmap. Then the pilot: 10 to 20 students through the January
  exams (docs/PRODUCT.md).

## The public demo, on the free plan (D31)

A separate service from the pilot, to send to friends: `deploy/demo/render.yaml`
runs `cps demo --public` on Render's free plan, in Frankfurt, with no disk. Each
visitor presses "Start the demo" and gets a made-up student of their own among five
made-up classmates. Everything is made again at each start, so nothing a visitor
types is kept for long; the home page asks for made-up details.

1. **Account.** Sign in at render.com with the GitHub account that owns the
   repository. The free plan asks for no payment **(check)**.
2. **Blueprint.** Dashboard, New, Blueprint **(check)**. Pick the repository and
   the `main` branch, and set the Blueprint's file path to
   `deploy/demo/render.yaml` **(check: the field may be called Blueprint Path)**.
   Render lists one free web service, `study-plan-demo`. Apply.
3. **Wait for the first deploy.** The log shows the build, then `making the demo
   store`, the links, and `serving on https://study-plan-demo….onrender.com`
   (Render adds letters to the name if `study-plan-demo` is taken). The line `The
   review page` is the address where reported posts are reviewed; keep it private.
4. **Try it**, on a phone: open the address, press Start the demo, look around.
   Then send the address to friends.

If the Blueprint screen has no file path, make the service by hand: New, Web
Service **(check)**, the repository, then Language Python 3, Branch `main`, Region
Frankfurt, Instance type Free, Build Command `pip install ".[web]"`, Start Command
`cps demo --public --host 0.0.0.0 --port $PORT --behind-proxy --store /tmp/cps-demo`,
Health Check Path `/health` (under Advanced), and an environment variable
`PYTHON_VERSION` = `3.13`.

**What to expect.** A free instance sleeps after about fifteen minutes without
visitors **(check)**; the next visit waits about a minute while it wakes and makes
the world again, and the students made before are gone (an old link says so and
offers a new one). Each push to `main` deploys the demo again (`autoDeploy: true`),
which also starts it from scratch. Render limits free instances' hours a month
**(check the current limit on its pricing page)**; one demo stays well inside it
because it sleeps when unused.
