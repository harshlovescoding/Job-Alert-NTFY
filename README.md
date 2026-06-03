# Interac Job Monitor

This small monitor checks Interac's Workday jobs API and tells you only when a newly posted role appears.

## 24/7 Cloud Monitoring

The project includes a GitHub Actions workflow at `.github/workflows/interac-job-monitor.yml`.

Once this folder is pushed to a GitHub repository, GitHub can run the monitor even when your computer is off:

- Every 15 minutes from 6:00 AM to 9:59 PM America/Toronto time
- Hourly from 10:00 PM to 5:59 AM America/Toronto time
- Manual runs from the GitHub Actions tab

The workflow checks every 15 minutes in UTC, then the workflow itself decides whether the current America/Toronto time should run or skip. This avoids daylight-saving-time drift.

Before enabling it, add this repository secret in GitHub:

```text
NTFY_TOPIC=<your ntfy topic>
```

Repository path:

```text
Settings -> Secrets and variables -> Actions -> New repository secret
```

The workflow needs repository write permission so it can commit updates to `data/seen_jobs.json`. That saved state prevents duplicate alerts for jobs it has already seen.

## How I Would Notify You

The best first version is a Codex automation that runs hourly and posts into this thread when it finds a new Interac listing. That keeps the alert inside Codex with the title, requisition ID, location, posted date, and application link.

Other notification paths we can add later:

- Email via SMTP or SendGrid
- SMS via Twilio
- Phone push notifications via ntfy
- Slack or Discord webhook
- macOS notification from a local scheduled job

The active phone notification path is now ntfy.

## ntfy Phone Notifications

Install the ntfy app and subscribe to this topic:

```text
the topic stored in data/ntfy_topic.txt
```

When a new Interac requisition appears, the monitor posts a high-priority notification to `https://ntfy.sh/<your-topic>`.

Run an ntfy-enabled check:

```bash
python3 scripts/interac_job_monitor.py --ntfy
```

The monitor sends ntfy notifications only when it detects a new requisition ID.

Send a test notification:

```bash
python3 scripts/interac_job_monitor.py --test-ntfy
```

## SMS Notifications

SMS uses Twilio Programmable Messaging. When the monitor finds new jobs, it sends one SMS containing the role title, requisition ID, location, posted date, and application link.

You need:

- A Twilio account
- A Twilio phone number that can send SMS to your phone
- Your Twilio Account SID and Auth Token
- Your destination phone number in E.164 format, such as `+14165550123`

Set these environment variables before running the monitor with `--sms`:

```bash
export TWILIO_ACCOUNT_SID="AC..."
export TWILIO_AUTH_TOKEN="..."
export TWILIO_FROM_NUMBER="+1..."
export SMS_TO_NUMBER="+1..."
```

Run an SMS-enabled check:

```bash
python3 scripts/interac_job_monitor.py --sms
```

The monitor sends SMS only when it detects a new requisition ID.

## Run It

Initialize the baseline once so existing jobs do not trigger alerts:

```bash
python3 scripts/interac_job_monitor.py --init-current
```

Check for new roles:

```bash
python3 scripts/interac_job_monitor.py
```

Exit codes:

- `0`: check succeeded and no new jobs were found, or baseline was initialized
- `1`: the monitor failed
- `2`: one or more new jobs were found

## Source

The monitor uses Interac's public Workday endpoint:

`https://interac.wd3.myworkdayjobs.com/wday/cxs/interac/Interac/jobs`

The application links point to:

`https://interac.wd3.myworkdayjobs.com/en-US/Interac`
