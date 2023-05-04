#!/usr/bin/env bash
# exit on error
set -o errexit

pip install .
pip install gunicorn

# python manage.py collectstatic --no-input
python demo/manage.py migrate
python demo/manage.py loaddata emailtemplate.json
python demo/manage.py loaddata demo.json