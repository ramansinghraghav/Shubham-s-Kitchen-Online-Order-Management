# Shubham's Kitchen

Restaurant ordering system built with Django. The project now supports PostgreSQL through `DATABASE_URL` and is ready to be deployed with Postgres as the primary database.

## Local setup

1. Create and activate a virtual environment.
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Copy `.env.example` to `.env` and set real values.
4. Run migrations:

```powershell
python manage.py migrate
```

5. Start the server:

```powershell
python manage.py runserver
```

## PostgreSQL migration from SQLite

The current project still has a local SQLite file at [db.sqlite3](</d:/My Projects/Shubham's Kitchen/db.sqlite3>). To move existing data into PostgreSQL, use this sequence.

1. Back up the SQLite database.
2. Make sure the SQLite schema is current:

```powershell
python manage.py migrate
```

3. Export data from SQLite:

```powershell
python manage.py dumpdata --natural-foreign --natural-primary --exclude contenttypes --exclude auth.permission > data.json
```

4. Create a PostgreSQL database, for example `shubham_kitchen`.
5. Update `.env`:

```env
DATABASE_URL=postgresql://postgres:your_password@localhost:5432/shubham_kitchen
```

6. Run PostgreSQL migrations:

```powershell
python manage.py migrate
```

7. Import the data into PostgreSQL:

```powershell
python manage.py loaddata data.json
```

8. Verify the app:

```powershell
python manage.py check --deploy
python manage.py runserver
```

## Deployment notes

- Set `DJANGO_DEBUG=False`
- Set a strong `DJANGO_SECRET_KEY`
- Set `DJANGO_ALLOWED_HOSTS`
- Set `DJANGO_CSRF_TRUSTED_ORIGINS`
- Configure PostgreSQL in `DATABASE_URL`
- Run `python manage.py collectstatic`

## Current status

- JWT auth endpoints are available
- Menu and order APIs are available
- Order status transitions are validated
- Razorpay order creation is scaffolded through environment-based configuration
