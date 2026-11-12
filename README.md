# KoupeLiy

A local barbershop walk-in queue built with Flask, SQLite, and plain HTML/CSS.

## Run locally

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m flask --app app run
```

- Customer check-in at [localhost:5000](http://127.0.0.1:5000/). Enter a name and select a service, then keep the ticket page open to check your position.
- Barber dashboard at [localhost:5000/barber](http://127.0.0.1:5000/barber). Refresh to see new arrivals and mark a customer complete to advance the queue.

Both pages offer English, Español, and Kreyòl Ayisyen. The first visit uses the browser's preferred supported language. Edit the labels in `translations.py`. SQLite creates `instance/queue.sqlite3` on the first queue request.

This starter is for local testing with sample names. Add barber authentication before exposing it to customers. Phone numbers, payments, SMS, estimated wait times, and automatic updates are future features.
