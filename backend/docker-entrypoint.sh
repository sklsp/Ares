#!/bin/sh
# Bring the schema up to date and make sure the demo catalog exists, then run
# whatever command the image was given.
set -e

echo "Applying database migrations..."
alembic upgrade head

echo "Seeding demo data if the catalog is empty..."
python -m app.seed --keep

exec "$@"
