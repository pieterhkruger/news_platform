# The Daily Indaba - Django News Platform

This is a Django news application with two main apps:

- **accounts**: registration, login, logout, profile management, password change, and password reset using Django's built-in authentication views.
- **daily_indaba**: news publishing, editorial approval, newsletters, subscriptions, reader comments, and REST API endpoints.

---

## Table of Contents

- [Reviewer Start Here](#reviewer-start-here)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Running with Docker](#running-with-docker)
- [What `python manage.py migrate` Sets Up Automatically](#what-python-managepy-migrate-sets-up-automatically)
- [Demo Accounts](#demo-accounts)
- [Authentication Notes](#authentication-notes)
- [Email Simulation](#email-simulation)
- [REST API](#rest-api)
- [Running Tests](#running-tests)
- [Project Structure](#project-structure)
- [References](#references)

---

## Reviewer Start Here

If reviewing the project rather than setting it up from scratch, start
with these three files before drilling into the app modules:

- `demo_credentials.txt`
  Contains the seeded demo accounts that let you exercise the browser flows,
  the editorial workflow, and the role-based permission boundaries quickly.
- `../Planning/REQUIREMENTS.md`
  Summarises the capstone brief as an implementation checklist, including the
  required and optional requirements used to assess completeness.
- `../Planning/CURRENT_IMPLEMENTATION_CLASSIFICATION.md`
  Maps the codebase to the requirement set and highlights where the project
  intentionally extends, interprets, or documents the capstone specification.

Taken together, these files answer three reviewer questions early: which
accounts to use, what the brief requires, and how the submitted project maps
to that brief.

---

## Prerequisites

- **Python 3.10+**
- A working virtual environment is recommended.
- **MariaDB** installed locally, with a database and user already created for
  this project.

Database connection settings are loaded automatically from a local `.env` file
via `python-dotenv`.

---

## Installation

Run all commands from this folder, which contains `manage.py`:

```text
news_platform/
├── manage.py
├── requirements.txt
├── accounts/
├── daily_indaba/
└── news_platform/
```

1. Create and activate a virtual environment.

Windows:

```powershell
python -m venv venv
venv\Scripts\activate
```

macOS / Linux:

```bash
python3 -m venv venv
source venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create a local `.env` file from the template and fill in your MariaDB
   credentials.

Windows:

```powershell
copy .env.example .env
```

macOS / Linux:

```bash
cp .env.example .env
```

Example values:

```text
DB_NAME=news_platform
DB_USER=news_platform_user
DB_PASSWORD=your_password
DB_HOST=127.0.0.1
DB_PORT=3306
```

---

## Quick Start

```bash
# 1. Apply migrations, create role groups, and auto-seed demo data
python manage.py migrate

# 2. Start the development server
python manage.py runserver
```

Then open:

- Home redirect: <http://127.0.0.1:8000/>
- News site: <http://127.0.0.1:8000/daily-indaba/>
- Accounts: <http://127.0.0.1:8000/accounts/login/>
- Admin: <http://127.0.0.1:8000/admin/>

---

## Running with Docker

This section describes how to run the project using Docker Compose instead of
a local Python environment. You need [Docker Desktop](https://www.docker.com/products/docker-desktop/)
installed and running.

### Prerequisites

- Docker Desktop installed and running.
- A `.env` file in the project root with at least `DB_PASSWORD` set.
  Copy the template and fill in your values:

  Windows:

  ```powershell
  copy .env.example .env
  ```

  macOS / Linux:

  ```bash
  cp .env.example .env
  ```

  The `DB_HOST` value in `.env` can be left as `127.0.0.1` — Docker Compose
  overrides it to `db` (the database container hostname) automatically.

### Starting the containers

```bash
docker-compose up --build
```

`--build` is only needed the first time, or after changing `requirements.txt`
or `Dockerfile`. Subsequent starts can use `docker-compose up`.

Wait until you see this line in the output before opening the browser:

```
web-1  | Starting development server at http://0.0.0.0:8000/
```

Then open: <http://localhost:8000/daily-indaba/>

### If you see a database error in the browser

The startup sequence uses `sleep 15` to give MySQL time to initialise, but on
a cold start MySQL sometimes takes longer. If the browser shows a
`ProgrammingError` like `Table '...' doesn't exist`, run migrations manually
while the containers are running:

```bash
docker exec news_platform-web-1 python manage.py migrate --no-input
```

Then refresh the browser. You can check the exact container name with:

```bash
docker ps
```

### Expected output during startup

After migrations run you will see a `ValidationError` about newsletters
requiring journalist authors. This is expected — it comes from the
post-migrate demo-seeding signal and does not affect the application.
The tables are created correctly; only the automatic seed data is skipped.
The server starts normally after the error.

### Stopping the containers

```bash
docker-compose down
```

---

## What `python manage.py migrate` Sets Up Automatically

Running `migrate` on a fresh database does more than create the schema.

### 1. Role groups and permissions

The **Readers**, **Journalists**, **Editors**, and **Publishers** groups are
created and kept in sync by a `post_migrate` signal in:

```text
daily_indaba/apps.py
daily_indaba/role_groups.py
```

This means the project does not rely on a manual admin-panel setup step to
create the role groups or attach model permissions.

If the groups are ever missing, you can recreate them manually:

```bash
python manage.py create_role_groups
```

### 2. Demo news data

After the role groups are synchronised, a second `post_migrate` hook seeds the
showcase data on a fresh local database by running:

```bash
python manage.py seed_demo_news
```

This creates demo users, publishers, articles, newsletters, subscriptions,
comments, and shared editorial categories.

Manual re-seed options:

```bash
# Create any missing demo records
python manage.py seed_demo_news

# Update existing demo records to match the JSON snapshot
python manage.py seed_demo_news --update-existing

# Use a custom shared password for all demo accounts
python manage.py seed_demo_news --password YourPassword123
```

Seed files live in:

```text
daily_indaba/seed_data/default_news_data.json
daily_indaba/seed_data/articles/
daily_indaba/seed_data/images/
```

---

## Demo Accounts

Fresh `migrate` runs create the showcase accounts automatically.

See:

```text
demo_credentials.txt
```

All demo accounts use the same password by default:

```text
demo1234
```

---

## Authentication Notes

This project follows Django's built-in authentication flow as closely as
possible for:

- login
- logout
- password change
- password reset

The accounts app uses `django.contrib.auth.views` rather than custom reset or
logout logic. Template names and redirect behaviour are customised where the
project needs site-specific behaviour, but token generation and validation are
left to Django.

Key settings:

```text
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "news:home"
PASSWORD_RESET_TIMEOUT = 3600
```

---

## Email Simulation

Local development defaults to Django's console email backend:

```python
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
```

This affects all project email flows, including password reset, subscriber
notifications, subscription confirmations, article rejection notices, and
all-articles plan confirmations. Instead of sending a real message, Django
prints the email to the terminal running `runserver`.

If the primary email backend fails, the project also supports a fallback
backend through `accounts/utils.py`. The primary and fallback backends can be
configured with:

- `DJANGO_EMAIL_BACKEND`
- `DJANGO_EMAIL_FALLBACK_ENABLED`
- `DJANGO_EMAIL_FALLBACK_BACKEND`
- `DJANGO_EMAIL_HOST`
- `DJANGO_EMAIL_PORT`
- `DJANGO_EMAIL_HOST_USER`
- `DJANGO_EMAIL_HOST_PASSWORD`
- `DJANGO_EMAIL_USE_TLS`
- `DJANGO_EMAIL_USE_SSL`
- `DJANGO_EMAIL_TIMEOUT`

---

## REST API

API routes live under `/api/`.

The API root at `/api/` returns a compact JSON index of the mounted API
endpoints. The project uses DRF's JSON renderer only, so the browser shows
JSON rather than the browsable HTML API.

Available endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/` | Return a JSON index of the mounted API routes |
| `POST` | `/api/token/` | Obtain a DRF token for a valid user |
| `GET` | `/api/articles/` | List accessible articles |
| `POST` | `/api/articles/` | Create an article as a journalist |
| `GET` | `/api/articles/subscribed/` | List articles available through reader subscriptions |
| `GET` | `/api/articles/<pk>/` | Retrieve one article |
| `PUT` | `/api/articles/<pk>/` | Replace an article if permitted |
| `PATCH` | `/api/articles/<pk>/` | Update an article if permitted |
| `DELETE` | `/api/articles/<pk>/` | Delete an article if permitted |
| `POST` | `/api/articles/<pk>/approve/` | Approve an article as an editor |
| `GET` | `/api/newsletters/` | List newsletters |
| `POST` | `/api/newsletters/` | Create a newsletter as a journalist or editor |
| `GET` | `/api/newsletters/<pk>/` | Retrieve one newsletter |
| `PUT` | `/api/newsletters/<pk>/` | Replace a newsletter if permitted |
| `PATCH` | `/api/newsletters/<pk>/` | Update a newsletter if permitted |
| `DELETE` | `/api/newsletters/<pk>/` | Delete a newsletter if permitted |

Authentication:

- The API uses DRF token authentication.
- Send `Authorization: Token <key>` on authenticated requests.
- Protected endpoints return `401 Unauthorized` when no token is supplied.

---

## Running Tests

```bash
python manage.py test
```

Targeted suites:

```bash
python manage.py test accounts
python manage.py test daily_indaba
```

The automatic showcase seed is skipped while Django is building the test
database, so the tests run against a clean schema.

---

## Project Structure

```text
news_platform/
├── manage.py
├── requirements.txt
├── README.md
├── .env.example
├── demo_credentials.txt
├── accounts/
│   ├── apps.py
│   ├── forms.py
│   ├── models.py
│   ├── tests/
│   ├── urls.py
│   ├── utils.py
│   ├── views.py
│   ├── migrations/
│   └── templates/accounts/
├── daily_indaba/
│   ├── admin.py
│   ├── api_serializers.py
│   ├── api_urls.py
│   ├── api_views.py
│   ├── apps.py
│   ├── bootstrap.py
│   ├── forms.py
│   ├── models.py
│   ├── role_groups.py
│   ├── tests/
│   ├── urls.py
│   ├── views/
│   ├── management/commands/
│   ├── migrations/
│   ├── seeding/
│   ├── seed_data/
│   ├── static/daily_indaba/
│   └── templates/
└── news_platform/
    ├── settings.py
    ├── urls.py
    ├── wsgi.py
    └── asgi.py
```
