# Deploying the pilot

*Checkpoint C1 of `docs/ROADMAP.md`. The owner does these steps: they create an
account, accept terms and start paying, which this repository never does on anyone's
behalf. Render's pages were not reachable from the environment this was written in,
so the menu names below are from Render's documentation as last known and are
marked **(check)** where they may have moved.*

## What gets deployed

`render.yaml` describes one web service:

| | |
|---|---|
| region | Frankfurt, so the data stays in the EU |
| plan | Starter, the smallest paid instance; a disk needs a paid instance |
| disk | 1 GB at `/var/data`: the store `cps.sqlite` and a week of daily backups |
| start | `cps web --host 0.0.0.0 --behind-proxy --db /var/data/cps.sqlite` |
| health check | `/health` |
| deploys | by hand (`autoDeploy: false`), after CI is green on `main` |

The app deletes plans 30 days after their last exam or deadline (checked every six
hours) and copies the store to `/var/data/backups` at start and once a day, keeping
seven days, which is what the privacy page promises. A copy on the same disk protects
against a bad release or a corrupted file, not against losing the disk; Render's own
disk snapshots **(check)** are the second line.

A service with a disk runs as one instance and restarts with a short interruption on
each deploy **(check)**. For a pilot of 10 to 20 students, one instance is ample;
the rate limits are in memory for that reason.

## Steps

1. **Account.** Sign up at render.com with the GitHub account that owns the
   repository. Add a payment method (Starter instance plus 1 GB of disk; check the
   current prices on the pricing page before confirming).
2. **Blueprint.** Dashboard, New, Blueprint **(check)**. Pick the repository and the
   `main` branch. Render reads `render.yaml` and lists one web service, `study-plan`,
   with a disk.
3. **Settings.** Render asks for the values marked `sync: false`:
   - `CPS_CONTACT`: the e-mail address students can write to about their data. It
     appears on the privacy page.
   - `CPS_BASE_URL`: leave empty, unless a custom domain is added later. The app then
     uses the address Render gives it (`RENDER_EXTERNAL_URL`).
   - `CPS_ADMIN_TOKEN`: a long random string (24 characters or more; for example the
     output of `python -c "import secrets; print(secrets.token_urlsafe(32))"`). The
     page where you review what students report is `/admin/<that string>`; keep it
     as private as a password. Left empty, there is no review page, and reported
     posts stay hidden after three reports until you set it (DECISIONS.md D20).
   - `ANTHROPIC_API_KEY` (optional): your key from Anthropic's console, for AI
     (DECISIONS.md D12). Left empty, there is no AI: tasks typed in words are read
     by rules and the weekly review uses its template. With it, each student still
     chooses in Settings. `CPS_AI_MONTHLY_CAP_USD` (default 10) stops calls for the
     rest of the month once the month's spending would pass it; `CPS_AI_MODEL`
     (default `claude-opus-5-5`; `claude-haiku-5-5` costs a fortieth as much per
     token) picks the model. Set a spending limit in Anthropic's console as well:
     the cap here counts what this server asked for, not what Anthropic billed.
4. **Deploy.** Apply. The build installs the package with the `web` extra; the log
   should end with `serving on http://0.0.0.0:<port>` and the health check turning
   green. If the build says Python 3.13 is not available in that form, set
   `PYTHON_VERSION` to a full version the error message lists (for example `3.13.7`)
   **(check)**.
5. **Try it on a phone.** Open the service's address (`https://study-plan-….onrender.com`),
   paste your own timetable link, check the exams, add a deadline, save. Then:
   - add the feed to Google Calendar on a computer (Other calendars, +, From URL) and
     check that the sessions appear, possibly hours later;
   - open one session's event in the calendar, follow its link, tap Done, and see
     Today change;
   - bookmark the Today page on the phone's home screen.
6. **Report back** what differed from these steps, so this page can be corrected.

## Operating it

- **Logs** show method, route, status and time, never a plan's address.
- **A release**: merge to `main`, wait for CI, then Manual Deploy in the dashboard
  **(check)**.
- **Read the pilot's numbers** once a week: from the service's shell **(check)**,
  `cps metrics --db /var/data/cps.sqlite` (or `--json`). Active students, sessions confirmed,
  return on the 7th and 30th day, streaks, the network, the month's AI spending
  (DECISIONS.md D15).
- **Restore** a backup: stop the service, copy `/var/data/backups/cps-<date>.sqlite`
  over `/var/data/cps.sqlite` from the service's shell **(check)**, start it again.
- **Delete everything**: delete the service and its disk in the dashboard.

## Before other students use it (C2)

- The owner reads and approves `/privacy`: who the controller is, the contact
  address, the host (Render, Frankfurt) and its own connection logs.
- Two or three friends go through step 5 with their own timetables; what confused
  them goes into the roadmap.

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
