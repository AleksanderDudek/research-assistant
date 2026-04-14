#!/bin/bash
# Drop and recreate the database schema (DEV ONLY – destroys all data)
set -e

echo "WARNING: This will delete all run history."
read -r -p "Continue? [y/N] " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Aborted."
    exit 1
fi

docker compose exec postgres psql -U agent -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
docker compose exec agent alembic upgrade head
echo "Database reset complete."
