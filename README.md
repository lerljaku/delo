# EloHell

Elo ratings for Magic: The Gathering players, calculated from tournament results that
admins upload. There are two ladders, **REL** and **REL + Casual**. Each player gets a
detail page with stats, an Elo chart, best and worst matchups, and tournament history.

* Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
* Decisions and open questions: [docs/DECISIONS.md](docs/DECISIONS.md)
* Elo model: [docs/ELO.md](docs/ELO.md)

```
backend/     Python API (one AWS Lambda), local dev server, tests
frontend/    static site (plain HTML + JS, no build step)
infra/       Terraform: S3, CloudFront, API Gateway, Lambda, DynamoDB, Cognito
docs/        architecture, Elo docs, release notes (deployed to the Docs page)
sample-data/ example tournament uploads in every supported format
```

## Run locally (no AWS needed)

Requires Python 3.12+. No packages are needed: the local server uses only the
standard library.

```bash
python backend/local_server.py --seed      # Windows: py backend/local_server.py --seed
```

Open http://127.0.0.1:8000. `--seed` loads `sample-data/` and the release notes. Data is
stored in `.localdata/`; delete that folder to start over.

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
site (Account → Sign in) and set a new password. Admins see **Upload** and **Admin** in the
navigation.

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

1. Edit constants or logic in `backend/elohell/elo.py` or `engine.py`, bump
   `MODEL_VERSION`, and update `docs/ELO.md`.
2. `terraform apply`
3. Admin page → **Recalculate everything**. All ratings are replayed from the stored
   source data in S3.

### Several people deploying

Use remote state so you don't overwrite each other's changes. Create an S3 bucket and a
DynamoDB lock table once, then uncomment the `backend "s3"` block in
`infra/versions.tf` and run `terraform init -migrate-state`. To get a separate test stack,
set `environment = "dev"` in a separate state or workspace.

### Custom domain (optional)

1. Request an ACM certificate for the domain in **us-east-1**.
2. In `infra/site.tf`, add `aliases = ["elohell.example"]` to the distribution and replace
   `viewer_certificate` with the ACM certificate ARN (`ssl_support_method = "sni-only"`).
3. Update the Cognito callback and logout URLs in `infra/auth.tf` and the
   `redirectUri`/`logoutUri` values in `site.tf`.
4. Add a DNS CNAME or alias record pointing to the CloudFront domain.

### Costs

At community scale this runs in the AWS free tier or close to it, typically **$0–1 per
month**. See [ARCHITECTURE.md §2](docs/ARCHITECTURE.md). API Gateway throttling
(`api_throttle_rate`) caps runaway costs. Cognito's built-in email sender is limited to
about 50 emails per day; configure SES if sign-ups exceed that.

### Tearing down

`terraform destroy`. The data bucket is versioned: empty it first, including old
versions, or destroy fails. **This deletes all tournament data.** Download
`s3://<data_bucket>/raw/` first if you want to keep it.

## Upload formats

Supported: pairings copied from Eventlink/Companion (the format of the files in `raw-data-eventlink/`), CSV, JSON and simple `Alice vs Bob 2-1` pairings. The format is documented on the site's Docs
page, and `sample-data/` has an example of each. Player names must be spelled
consistently across tournaments; the upload preview lists new players so typos are easy
to catch.
