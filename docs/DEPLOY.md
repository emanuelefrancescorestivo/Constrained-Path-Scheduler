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
3. **Three settings.** Render asks for the values marked `sync: false`:
   - `CPS_CONTACT`: the e-mail address students can write to about their data. It
     appears on the privacy page.
   - `CPS_BASE_URL`: leave empty, unless a custom domain is added later. The app then
     uses the address Render gives it (`RENDER_EXTERNAL_URL`).
   - `CPS_ADMIN_TOKEN`: a long random string (24 characters or more; for example the
     output of `python -c "import secrets; print(secrets.token_urlsafe(32))"`). The
     page where you review what students report is `/admin/<that string>`; keep it
     as private as a password. Left empty, there is no review page, and reported
     posts stay hidden after three reports until you set it (DECISIONS.md D20).
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
- **Restore** a backup: stop the service, copy `/var/data/backups/cps-<date>.sqlite`
  over `/var/data/cps.sqlite` from the service's shell **(check)**, start it again.
- **Delete everything**: delete the service and its disk in the dashboard.

## Before other students use it (C2)

- The owner reads and approves `/privacy`: who the controller is, the contact
  address, the host (Render, Frankfurt) and its own connection logs.
- Two or three friends go through step 5 with their own timetables; what confused
  them goes into the roadmap.
