#!/bin/sh

echo "production"
export BUILD_ENV=production

# Completely re-build all images from scatch without using build cache
docker-compose build --no-cache
docker-compose up --force-recreate -d
