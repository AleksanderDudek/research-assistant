#!/bin/bash
# Run Alembic migrations then hand off to CMD
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting agent..."
exec "$@"
