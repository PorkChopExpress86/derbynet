# Raspberry Pi timer and photo stand

A Raspberry Pi beside the track can run the timer client or the photo stand as a container, so the Pi needs only Docker and one settings file. Each starts again by itself when the Pi starts. The server runs on another computer ([Install with Docker](install-docker.md)); a Pi is not supported as the server (D21).

- The timer image runs `derby-timer.jar`, which talks to the timer over its USB serial cable ([Timer operation](timer-operation.md)). The browser timer is the supported timer client (D4). The jar is for a timer the browser cannot reach, or a Pi left at the track.
- The photo stand image runs `extras/scripts/photostand.sh`: scan a racer's barcode, and the camera takes the photo and sends it to the server ([The automated photo stand](photo-stand.md)).
- A Pi kiosk needs no image. Point its browser at the kiosk page.

## What you need

- A Raspberry Pi 4 or newer, with the 64-bit Raspberry Pi OS. The images are built for 64-bit Arm only (`linux/arm64`).
- The Pi on the same network as the server.
- For the timer: the timer and its USB serial cable.
- For the photo stand: a USB barcode scanner, and a camera that `gphoto2` or `fswebcam` drives. Optional: a speaker, so the stand says what it is doing.

## Install Docker on the Pi

On the Pi, install Docker with its install script, and let your user run it:

```sh
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Log out and in again. The script also sets Docker to start when the Pi starts, which starts the containers again after a reboot.

## Which image

The images are published next to the server image, as ghcr.io/porkchopexpress86/derbynet_timer and ghcr.io/porkchopexpress86/derbynet_photostand. The Compose files below use the `latest` tag, which only a full release sets (D89). Until the first one, add a line to `.env` naming the newest test release from the project's [Releases page](https://github.com/PorkChopExpress86/derbynet/releases): `DERBYNET_TIMER_IMAGE=ghcr.io/porkchopexpress86/derbynet_timer:` and the release's tag for the timer, or `DERBYNET_PHOTOSTAND_IMAGE=ghcr.io/porkchopexpress86/derbynet_photostand:` and the tag for the photo stand.

## Set up the timer

1. Make a folder for it, and download the Compose file:

```sh
mkdir -p ~/derbynet-timer && cd ~/derbynet-timer
curl -fsSLO https://raw.githubusercontent.com/PorkChopExpress86/derbynet/master/extras/docker/compose.timer.yaml
```

2. Plug in the timer, and find its serial port. It is usually `/dev/ttyUSB0` or `/dev/ttyACM0`:

```sh
ls -l /dev/serial/by-id/
```

3. Write `.env` in the same folder. `DERBYNET_SERVER` is the server's address with no slash at the end, and plain `http://`:

```sh
DERBYNET_SERVER=http://192.168.1.20
DERBYNET_TIMER_DEVICE=/dev/ttyUSB0
```

`TIMER_ARGS` adds `derby-timer.jar` options, as `/etc/derbynet.conf` did on older installs. For a timer that never answers, name its type: `TIMER_ARGS=-d FastTrackPSeries`. If you gave Timer a password on Roles and passwords, add `-p` and the password. `-h` lists every option ([Timer operation](timer-operation.md#derby-timerjar)).

4. Start it:

```sh
docker compose -f compose.timer.yaml up -d
docker compose -f compose.timer.yaml logs -f
```

The log shows "Waiting for" the server until it answers, then the jar's own lines. The timer page on the server shows the timer as connected. The jar's log files stay in the `timer-logs` volume.

The timer must be plugged in when the container starts, since Docker passes the serial port in at the start. If you plug it in later, run `docker compose -f compose.timer.yaml restart`.

## Set up the photo stand

1. Make a folder for it, and download the Compose file:

```sh
mkdir -p ~/derbynet-photostand && cd ~/derbynet-photostand
curl -fsSLO https://raw.githubusercontent.com/PorkChopExpress86/derbynet/master/extras/docker/compose.photostand.yaml
```

2. Plug in the scanner, and find its name:

```sh
ls /dev/input/by-id/
```

The scanner is usually the entry with the maker's name that ends in `-event-kbd`.

3. Write `.env` in the same folder. Each setting is the `/etc/derbynet.conf` setting of the same name ([Point it at the server](photo-stand.md#point-it-at-the-server)). One you leave out keeps the photo stand's default.

```sh
DERBYNET_SERVER=http://192.168.1.20
PHOTO_CAPTURE=gphoto2
PHOTO_REPO=head
PHOTO_CHECKIN=1
BARCODE_SCANNER_DEVS=usb-Your_Scanner_Name-event-kbd
```

Set `BARCODE_SCANNER_DEVS`. With it, the stand takes the scanner for itself, so a scan types nowhere else on the Pi. Without it, the stand reads the first device in `/dev/input/by-id`, which may be the Pi's keyboard.

4. Start it:

```sh
docker compose -f compose.photostand.yaml up -d
docker compose -f compose.photostand.yaml logs -f
```

The log shows "Successfully logged in". Scan a racer's barcode: the log shows the scan, the photo and the upload. The photos and the stand's `uploads.log` and `checkins.log` stay in the `photostand-photos` volume.

### Cameras

- A digital SLR, through `gphoto2`, needs nothing more: the Compose file passes the USB bus in, so a camera plugged in after the start works too.
- A webcam, through `fswebcam`: set `PHOTO_CAPTURE=fswebcam` in `.env`, and remove the `#` from the two `devices` lines in `compose.photostand.yaml`.
- A Canon camera running CHDK: `chdkptp` is not in the image. Unpack a build of chdkptp for 64-bit Arm into a folder named chdkptp beside the Compose file, so that its chdkptp.sh is in that folder. Remove the `#` from the chdkptp line in `compose.photostand.yaml`, and set `PHOTO_CAPTURE=chdkptp`.

On a Pi with the desktop, its file manager can take the camera first. Stop that once, as the user who logs in:

```sh
systemctl --user stop gvfs-daemon
systemctl --user mask gvfs-daemon
```

### Your own functions

The photo stand reads each `.conf` file in the `derbynet.d` folder beside the Compose file after its settings. Put a replacement for one of its functions there, such as the CHDK zoom in [Point it at the server](photo-stand.md#point-it-at-the-server).

### What the container does not do

- `QUITQUITQUIT` does not shut the Pi down. A container cannot, so the stand announces it and keeps running. Shut the Pi down from its menu, or with `sudo shutdown -h now`.
- `ADJUST_CLOCK` does nothing: a container cannot set the Pi's clock. Raspberry Pi OS sets it from the network.

## After a reboot

Both containers start again by themselves, since Docker starts with the Pi and the Compose files say `restart: unless-stopped`. To stop one for good, run `docker compose -f <its Compose file> down` in its folder.

## Update

In the container's folder, set the new tag in `.env` if you named one, then:

```sh
docker compose -f compose.timer.yaml pull
docker compose -f compose.timer.yaml up -d
```

Use `compose.photostand.yaml` for the photo stand.
