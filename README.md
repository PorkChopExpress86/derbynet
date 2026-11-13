# Please visit us at [https://derbynet.org](https://derbynet.org).

![icon](https://raw.githubusercontent.com/jeffpiazza/derbynet/master/website/img/derbynet-300.png)

# Developing locally

The server is the Django port in `server/`; `server/README.md` covers its
tests and tools. Docker is the only install (D3), and you can develop against
the same image a race runs, with your checkout's `server/` mounted into it.
You need Docker (Docker Desktop on a Mac or Windows) and nothing else.

1. From the root of the cloned repository, build the image and start it with
   the `image` profile of `server/compose.yaml`:

   ```sh
   docker compose -f server/compose.yaml --profile image up --build image
   ```

   It serves `http://localhost:8081/` and `https://localhost:8443/`
   (`DERBYNET_IMAGE_HTTP_PORT` and `DERBYNET_IMAGE_HTTPS_PORT` change them).
   The first visit sets up an event.

2. Edit the code. `server/` is mounted read-only over the image's copy, so
   a template edit shows on the next page load (the service sets
   `DERBYNET_DEBUG=1`, so Django reads templates afresh), and gunicorn restarts
   itself after a Python edit (`GUNICORN_RELOAD=1`). Static files (CSS and
   JavaScript) are collected when the image is built, so rerun the command
   above with `--build` after changing them, and after a change to
   `server/uv.lock`.

3. Your data stays in the `image-data` volume between runs, as a race's does
   in its data volume. `docker compose -f server/compose.yaml --profile image
   down -v` deletes it.

The same `server/compose.yaml` also runs the PHP server and Django's
`runserver` side by side (`server/README.md`, "Run both servers").
`server/docker/README.md` describes the image itself: its settings, TLS,
Postgres, and backups.
