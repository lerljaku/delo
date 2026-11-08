# Delo

Elo ratings for Magic: The Gathering players, calculated from tournament results that
signed-in users upload, at [mtgdelo.com](https://mtgdelo.com) once the domain is registered. There are two
ladders, **REL** and **REL + Casual**, each for all formats together and per format (Modern,
Limited, Duel Commander, EDH, Legacy, Vintage, Premodern). Each player gets a
detail page with stats, an Elo chart, best and worst matchups, and tournament history.

* Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
* Decisions and open questions: [docs/DECISIONS.md](docs/DECISIONS.md)
* Elo model: [docs/ELO.md](docs/ELO.md)

```
backend/     Python API (one AWS Lambda), local dev server, tests
frontend/    static site (plain HTML + JS, no build step)
infra/       Terraform: S3, CloudFront, API Gateway, Lambda, DynamoDB, Cognito
docs/        architecture, Elo docs, release notes (deployed to the Docs page)
```

## Run locally (no AWS needed)

Requires Python 3.12+. No packages are needed: the local server uses only the
standard library.

```bash
python backend/local_server.py --seed      # Windows: py backend/local_server.py --seed
```

Open http://127.0.0.1:8000. `--seed` loads the release notes and the tournaments in
`raw-data-eventlink/`: real Eventlink pastes, one `.txt` file per event. The folder is kept
out of git because it contains players' names, so on a fresh clone create it and add your
own exports, or seed from another folder with `--source DIR`. Data is stored in
`.localdata/`; delete that folder to start over.

The first seed writes `raw-data-eventlink/events.json` with the
name, date and type of every event, guessed from the file names. Files that contain several
events are split into `file.txt#1`, `file.txt#2`, … Entries with a `todo` line had no date
in the file name. Fix them, set `"skip": true` to leave an event out, then reload with
`py backend/import_events.py --target local --replace` (or delete `.localdata/` and seed
again).

Sign-in is faked locally. Use the **Dev user** menu in the top-right corner to act as an
admin or as a regular player.

## Tests

```bash
python -m unittest discover -s backend/tests            # core tests, no dependencies
pip install -r backend/requirements-dev.txt             # optional: adds moto
python -m unittest discover -s backend/tests            # now also runs the AWS storage tests
```

CI (`.github/workflows/ci.yml`) runs the tests, a JS syntax check, and
`terraform fmt`/`validate` on every push and pull request.

> Windows on ARM: `moto` depends on `cryptography`, which has no ARM wheel for
> Python 3.14 yet. Use an x64 Python 3.12/3.13 for the AWS tests, or rely on CI.

## Deploy to AWS

### Prerequisites

* An AWS account and credentials (`aws configure` or `AWS_PROFILE`) with admin rights
  for the first deploy
* [Terraform](https://developer.hashicorp.com/terraform/install) 1.6 or newer
* Python is **not** needed for deploying. Terraform zips `backend/` itself.

### First deploy

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars   # set admin_emails, github_repo, region
terraform init
terraform plan
terraform apply
```

The first apply takes about 5–10 minutes, mostly because CloudFront is slow to create.
Outputs:

* `site_url`: the website (`https://xxxx.cloudfront.net`)
* `cognito_login_domain`, `cognito_user_pool_id`, `cognito_client_id`

Each email in `admin_emails` gets an invitation with a temporary password. Sign in on the
site (Account → Sign in) and set a new password. Admins see **Admin** in the navigation;
any signed-in user can upload results, but only admins can correct or delete them.

### Importing tournament data

`backend/import_events.py` loads every Eventlink paste in `raw-data-eventlink/` into the
deployed S3 bucket and DynamoDB tables, then recalculates Elo once. It needs Python, the
dev requirements and AWS credentials. It finds the table and bucket names in the deployed
Lambda's settings (`terraform output lambda_function`, `mtgdelo-prod-api` by default).

```bash
pip install -r backend/requirements-dev.txt
python backend/import_events.py --target aws --dry-run     # parse and list, store nothing
python backend/import_events.py --target aws               # import
```

Event names, dates and types come from `raw-data-eventlink/events.json`, as described in
[Run locally](#run-locally-no-aws-needed). Events that are already stored are skipped, so
running it again only adds new files. After changing a date, name or type, run it with
`--replace`: this **deletes every tournament** in the stack, including ones uploaded through
the website, and imports the folder again. Other options: `--function`, `--profile`,
`--region`, `--source DIR`, and `--target local` for `.localdata/`.

When the parser changes what an Eventlink paste means, `backend/reparse_eventlink.py` reads
every stored Eventlink tournament again from its raw text. It replaces those whose matches
change, keeping their details and decks, then recalculates once. Website and extension
uploads are included. Same options as above; start with `--dry-run`.

### Updating

Change code, then run `terraform apply` again. Terraform re-uploads changed frontend files
and redeploys the Lambda when `backend/` changes. Frontend files are cached for 5 minutes,
so no CloudFront invalidation is needed.

### Adding or removing admins

Edit `admin_emails` and run `terraform apply`, or add a user to the `admin` group in the
Cognito console.

### Release notes

Add `docs/release-notes/<version>.md`:

```
version: 0.2.0
title: Short title
date: 2026-10-15
summary: One line
---
## Markdown body
```

`terraform apply` publishes it to the Docs page. Admins can also publish from the Admin
page without a deploy.

### Changing the Elo model

1. Edit constants or logic in `backend/delo/elo.py` or `engine.py`, bump
   `MODEL_VERSION`, and update `docs/ELO.md`.
2. `terraform apply`
3. Admin page → **Recalculate everything**. All ratings are replayed from the stored
   source data in S3. Until then, uploads also fall back to a full recalculation, because
   incremental updates only continue from ratings computed with the current `MODEL_VERSION`.

### Several people deploying

Use remote state so you don't overwrite each other's changes. Create an S3 bucket and a
DynamoDB lock table once, then uncomment the `backend "s3"` block in
`infra/versions.tf` and run `terraform init -migrate-state`. To get a separate test stack,
set `environment = "dev"` in a separate state or workspace.

### Islands (one-time migration)

Leaderboards, tournaments and upcoming tournaments are per island (a separately rated group of
players, e.g. a country; see ARCHITECTURE.md). After deploying islands, move the existing data
into "Czech Island" once:

```
py backend/migrate_islands.py --target aws --dry-run   # shows what would change
py backend/migrate_islands.py --target aws
```

It creates the island, assigns every tournament and upcoming tournament to it and recalculates all
ratings. Running it again changes nothing. Until it runs, the site works as before, without an island
picker. More islands: Admin → Islands; `import_events.py --island <id>` imports into a given island.

### Payments (Stripe)

Memberships work without any Stripe code in the browser: Payment Links plus a webhook.

1. In Stripe, create two products with **Payment Links**: Supporter, $5 one-time, and
   Diamond, $5/month recurring. Optionally set each link's confirmation page to
   `<site_url>/membership.html`.
2. Turn on the **customer portal** (Settings → Billing → Customer portal), with cancellation
   at the end of the billing period, and copy its login link.
3. Add a webhook endpoint `<site_url>/api/stripe/webhook` with the events
   `checkout.session.completed`, `customer.subscription.created`,
   `customer.subscription.updated` and `customer.subscription.deleted`. Copy its signing
   secret (`whsec_...`).
4. In `infra/terraform.tfvars` set `stripe_supporter_link`, `stripe_diamond_link`,
   `stripe_portal_link` and `stripe_webhook_secret`, then `terraform apply`.

Test it end to end with Stripe's test mode links and secret first. Admins can also grant a
membership by hand (Admin → Player moderation).

### Custom domain (mtgdelo.com)

1. Register the domain in Route 53 (Route 53 → Domains → Register). This also creates its
   hosted zone (about $0.50/month).
2. Set `domain_name = "mtgdelo.com"` in `infra/terraform.tfvars` and run `terraform apply`.
   Terraform requests the certificate in us-east-1, validates it through DNS, points
   `mtgdelo.com` and `www.mtgdelo.com` at CloudFront (www redirects to the bare domain) and
   adds the domain to the Cognito login URLs. The `cloudfront_url` output keeps working.

With another DNS provider, create the hosted zone for the domain in Route 53 and point the
domain's name servers at it.

### Costs

At community scale this runs in the AWS free tier or close to it, typically **$0–1 per
month**. See [ARCHITECTURE.md §2](docs/ARCHITECTURE.md). API Gateway throttling
(`api_throttle_rate`) caps runaway costs. Cognito's built-in email sender is limited to
about 50 emails per day; configure SES if sign-ups exceed that.

### Tearing down

`terraform destroy`. The data bucket is versioned: empty it first, including old
versions, or destroy fails. **This deletes all tournament data.** Download
`s3://<data_bucket>/raw/` first if you want to keep it. The tables, data bucket and user
pool have `prevent_destroy`: remove it (or `terraform state rm` them) first.

## Upload formats

Supported: pairings copied from Eventlink/Companion (the format of the files in `raw-data-eventlink/`), CSV, JSON and simple `Alice vs Bob 2-1` pairings. Each format is documented with an example on the site's Docs
page. Player names must be spelled
consistently across tournaments; the upload preview lists new players so typos are easy
to catch.

## Chrome extension: upload from Eventlink

`extension/` is a Chrome extension (👑 icon) that uploads an Eventlink tournament in one
click, instead of copying every round's pairings into the upload page:

1. In Chrome open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**
   and pick the `extension/` folder. Site visitors get it from the bottom of the upload page
   instead: `terraform apply` publishes it zipped as `/mtgdelo-extension.zip` (`infra/site.tf`).
   That link only works on the deployed site, not on the local dev server.
2. Open the tournament in Eventlink (any of its pages, signed in as usual) and click the 👑
   button in the toolbar.
3. Check the name, date, format, type and island, then click **Upload**. If the tournament was
   listed in Upcoming tournaments, pick it under **Planned tournament** (the last 2 weeks) to
   fill these in.

The extension reads the results in the Eventlink tab, so the Eventlink login never leaves
the browser. Uploading needs **no mtgdelo account** (`POST /api/eventlink`, throttled; see
docs/DECISIONS.md, "Who uploads"). mtgdelo keeps one tournament per Eventlink event. Uploading
again with the same results changes nothing, and after more rounds it replaces the earlier
upload. Events with unfinished matches are refused. It talks to https://mtgdelo.com; for a
local server set **Settings → mtgdelo site** in the popup to `http://localhost:8000`. More in
`extension/README.md`.
