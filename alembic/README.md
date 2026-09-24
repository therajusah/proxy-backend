# Database migrations

The application keeps an in-memory store for the local vertical slice. This Alembic setup defines the PostgreSQL persistence boundary for the next deployment step.

```bash
alembic upgrade head
```

Set `DATABASE_URL` before running migrations. The schema keeps cookies and other short-lived session material out of PostgreSQL; those belong in the Redis state backend with TTLs.
