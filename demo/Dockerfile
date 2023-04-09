FROM python:3.11

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN pip install --upgrade pip
RUN pip install django-helpdesk
RUN pip uninstall pinax_teams -y

WORKDIR /usr/src/app
COPY . .

RUN python manage.py migrate --noinput
RUN python manage.py loaddata emailtemplate.json
RUN python manage.py loaddata demo.json

CMD [ "python", "manage.py", "runserver", "0.0.0.0:8000" ]