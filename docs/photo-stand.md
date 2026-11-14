# The automated photo stand

A photo stand takes each racer's photo, or each car's, when someone scans a barcode, and sends the photo to the server for that racer. It runs on a Raspberry Pi with a barcode scanner and a camera. The script that drives it is `extras/scripts/photostand.sh`. It is shown at work in a video: <https://youtu.be/aR5vDUBemx4?t=213>.

Photo stands built for earlier versions of DerbyNet keep working with this server. They use the same requests the server has always answered (ADR-02). Point one at the new server's address, as in [Point it at the server](#point-it-at-the-server). Ready-made container images for the Pi's photo stand and timer are planned, in #59.

You can also take photos at check-in, with a phone, and need no photo stand.

## What you need

- A Raspberry Pi running Raspberry Pi OS.
- A USB barcode scanner.
- A camera, one of:
  - a webcam;
  - a digital SLR camera, with its USB cable;
  - a Canon camera running CHDK, the Canon Hack Development Kit (<https://chdk.fandom.com/wiki/CHDK>).
- Optional: a speaker on the Pi's audio jack, so the Pi can say what it is doing when it runs with no screen.

## Barcodes

The photo stand finds the racer by the barcode it scans. DerbyNet no longer prints barcodes (D6), and check-in no longer scans them (D13), so make labels with any barcode maker, such as a label printer's software. Each barcode holds `PWD` and then the racer's car number: `PWD101` for car 101. The photo is for the one racer with that car number.

A few barcodes control the photo stand itself:

| Barcode | What it does |
|---|---|
| `PWDcheckin-ON` | Checks in each racer whose barcode is scanned, from now on. |
| `PWDcheckin-OFF` | Stops checking racers in. |
| `PWDspeedtest` | Tests how fast photos upload. |
| `QUITQUITQUIT` | Shuts the Pi down. |

`extras/scripts/` holds ready images of these barcodes, such as `PWDcheckin-ON.png`, to print.

## Set up a new photo stand

These steps install the photo stand's scripts from a copy of the DerbyNet repository on the Pi.

1. Copy the scripts into place:

   ```sh
   git clone https://github.com/PorkChopExpress86/derbynet.git
   cd derbynet/extras
   sudo mkdir -p /usr/share/derbynet
   sudo cp -r autostart conf scripts sounds tools /usr/share/derbynet/
   sudo cp derbynet.conf /etc/derbynet.conf
   ```

2. Install the program for your camera:
   - A webcam: `sudo apt install -y fswebcam`
   - A digital SLR: `sudo apt install -y gphoto2`
   - A CHDK camera: download chdkptp from <https://app.assembla.com/spaces/chdkptp/documents>, and unzip it into `/opt/chdkptp`. Install its USB library with `sudo apt install -y libusb-0.1-4`. Then make it a command with `sudo ln -s /opt/chdkptp/chdkptp.sh /usr/local/bin/chdkptp`. It must be a symbolic link, since the script finds its other files beside it.

3. Optional, for a Pi with no screen: install speech and sounds, with `sudo apt install -y flite mpg123`. Bring a keyboard and a screen anyway, in case something needs fixing at the race.

4. Start the photo stand when the Pi starts:

   ```sh
   mkdir -p ~/.config/autostart
   cp /usr/share/derbynet/autostart/photostand.desktop ~/.config/autostart/
   ```

## Point it at the server

Edit `/etc/derbynet.conf` on the Pi. Remove the `#` at the start of each line you set.

- `DERBYNET_SERVER`: the server's address, with no slash at the end, such as `DERBYNET_SERVER="http://192.168.1.20"`. Use plain `http://`.
- `PHOTO_CAPTURE`: `gphoto2` (the default), `chdkptp`, or `fswebcam`.
- `PHOTO_REPO`: `head` for racer photos, or `car` for car photos.
- `PHOTO_USER` and `PHOTO_PASSWORD`: the role the photo stand logs in as. The default is Photo, with the password `flashbulb`. If you change the Photo password (see [Roles and passwords](roles-and-passwords.md)), change it here too.
- `PHOTO_CHECKIN=1`: also checks each racer in when the barcode is scanned. The Photo role may check racers in this way.
- `AUTOCROP=1`: asks the server to crop each photo to the face.

`/etc/derbynet.conf` can also replace the script's own functions. This one zooms a CHDK camera before each car photo:

```sh
unset prepare_camera_before_shot
prepare_camera_before_shot() {
  chdkptp -c -e"rec" -e"luar set_zoom(40)"
}
```

## Where the photos go

The server keeps each photo in the active event's racer photo or car photo folder, and gives it to the racer the barcode names. The Racer Photos and Car Photos pages show them, to crop or turn.
