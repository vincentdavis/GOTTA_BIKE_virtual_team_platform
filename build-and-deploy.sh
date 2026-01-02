#!/bin/bash
uv run python manage.py tailwind build
#git add -f theme/static/css/dist/styles.css
#git commit -m "Build CSS" --allow-empty
#git push
railway up
