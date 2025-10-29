# Please visit us at [https://derbynet.org](https://derbynet.org).

![icon](https://raw.githubusercontent.com/jeffpiazza/derbynet/master/website/img/derbynet-300.png)

# Developing locally

DerbyNet can be developed on your workstation either with the bundled Docker
tooling or by binding the official Docker image to your checked-out source
tree. Pick whichever option best matches your workflow.

## Option A: first-party Docker Compose stack

The repository ships with a first-party Dockerfile and Compose file so you can
run DerbyNet without installing PHP or Apache locally.

1. (Optional) Install [Apache Ant](https://ant.apache.org/) if you plan to
   regenerate PDF assets or build the optional timer tooling.
2. Build the development containers and start DerbyNet:

   ```bash
   docker compose up --build -d
   ```

   The web UI will be available at [http://localhost:8080](http://localhost:8080)
   by default. Set `DERBYNET_HTTP_PORT` before running the command to publish a
   different host port.
3. Application data (databases, photos, exports, etc.) is stored in the
   `derbynet-data` volume at `/var/lib/derbynet` inside the container. You can
   inspect or back up that volume with `docker volume inspect derbynet-data` or
   by binding it to a host directory in `docker-compose.yml`.

### Running behind a reverse proxy

For deployments where a proxy such as Traefik, Nginx, or Caddy handles TLS and
host routing, remove or override the `ports` section in `docker-compose.yml`
and attach the `derbynet` service to your proxy network. For example:

```bash
DERBYNET_HTTP_PORT= # prevents publishing a host port
docker compose up --build -d
docker network connect <proxy-network> derbynet-derbynet-1
```

Alternatively, copy `docker-compose.yml`, remove the `ports` stanza, and add a
`networks` entry that references your existing proxy network before running
`docker compose up`.

## Option B: bind-mount against the published Docker image

If you prefer to use the `jeffpiazza/derbynet_server` image directly while
editing the sources on your host, follow the original development flow:

1. Install [Apache Ant](https://ant.apache.org/).
   1. You can [install WSL](https://learn.microsoft.com/en-us/windows/wsl/install)
      and run:

      ```bash
      sudo apt-get update
      sudo apt-get install ant
      ```

2. Execute `ant generated` from the root of the cloned repository.  (This build
   target includes a step to generate PDF files from their ODF source files.
   This step will be silently skipped if the LibreOffice/OpenOffice `soffice`
   application is not available.)

3. If desired, do one or both of the following.  (If you do neither, you won't
   be able to connect to a hardware timer.)

   1. Execute `ant timer-in-brower` to build the in-browser timer interface.
   2. Execute `ant timer-jar` to build the derby-timer.jar timer interface.

4. Instantiate the docker container, but use your local sources rather than
   those deployed in the container.  _**PATH_TO_YOUR_DATA**_ is a local
   directory where you'd like databases, photos, and other data files to be
   stored. _**PATH_TO_YOUR_REPOSITORY**_ is the path to your local cloned
   repository.

   ```powershell
   docker run --detach -p 80:80 -p 443:443 \
     --volume [** PATH TO YOUR DATA **]\\lib\\:/var/lib/derbynet \
     --mount type=bind,src=[** PATH TO YOUR REPOSITORY **]\\website\\,target=/var/www/html,readonly \
     jeffpiazza/derbynet_server
   ```
