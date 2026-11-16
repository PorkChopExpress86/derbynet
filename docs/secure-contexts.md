# Secure contexts and DerbyNet

Browsers give a web page the camera and the serial port only when the page is secure. The page must come over HTTPS, or from the same computer as `localhost`. The web standard calls such a page a secure context.

On a race's own network, DerbyNet serves both plain HTTP and HTTPS: Caddy, the container in front of the server, serves HTTPS (D107). This guide says which pages need HTTPS, and how each phone comes to trust it.

## Which pages need HTTPS

| Page | Needs | Over plain HTTP |
|---|---|---|
| The browser timer (`/timer.php`) | Web Serial, to talk to the timer | The page sends you to a page that says to use Chrome or Edge. |
| Replay (`/replay/`) | The camera | The browser offers no camera. |
| The replay camera (`/replay/camera/`) | The camera | The browser offers no camera. |
| Check-in and the photo pages, "Use the live camera" | The camera | The button is hidden. "Take racer photo" and "Take car photo" still work: they open the phone's own camera app. |

Every other page works over plain HTTP: check-in without the live camera, the race dashboard, the kiosks, standings, and these guides. Kiosk screens, `derby-timer.jar`, and the Raspberry Pi scripts use plain HTTP, and the server never redirects them to HTTPS.

On the computer that runs the server, `http://localhost/` is already secure. Every other device uses the HTTPS address, such as `https://192.168.1.20/`. The one-command `docker run` setup has no HTTPS, so there the four pages above work only at `http://localhost/` ([Plain HTTP with one command](install-docker.md#plain-http-with-one-command)).

## Trust this install's certificate authority

HTTPS needs a certificate, which tells the browser who the server is. On its first start, Caddy makes a certificate authority for this install, and keeps it in the `derbynet_caddy` volume. It signs the certificates for `https://localhost/` and for the address in `DERBYNET_ADDRESS` ([Start DerbyNet](install-docker.md#start-derbynet)). Every install has its own authority, and nobody else has its key.

No browser knows this authority until you install its root, once on each phone, tablet and laptop. After that, the browser trusts the HTTPS address with no warning, for as long as the `derbynet_caddy` volume is kept. Caddy serves the root at `/derbynet-ca.crt`, such as `http://192.168.1.20/derbynet-ca.crt`. Without the root, each browser warns the first time it opens the HTTPS address, and you can accept the warning instead, as below.

These steps are not tested by the project's checks; menu names change between versions.

### Install it on an iPhone or iPad

1. Open `http://192.168.1.20/derbynet-ca.crt` in Safari. Safari asks whether to allow a configuration profile. Tap "Allow".
2. Open Settings. Tap "Profile Downloaded", then "Install", and enter the passcode.
3. In Settings, open General, then About, then Certificate Trust Settings. Turn on full trust for "Caddy Local Authority".

### Install it on an Android phone

1. Open `http://192.168.1.20/derbynet-ca.crt` in Chrome, which saves the file.
2. Open Settings, then Security, then Encryption and credentials, then Install a certificate, then CA certificate. Choose the saved file.

Phone makers name these menus differently. Search Settings for "CA certificate".

### Install it on a laptop

- On a Mac, open the saved file. Keychain Access adds it. Double-click "Caddy Local Authority", open Trust, and choose "Always Trust".
- On Windows, open the saved file, choose "Install Certificate", and place it in "Trusted Root Certification Authorities".
- Firefox keeps its own list: Settings, Privacy & Security, Certificates, View Certificates, Authorities, Import.

### Or accept the warning

Without the root, each browser warns the first time, and remembers the answer. On an iPhone, Safari says "This Connection Is Not Private": tap "Show Details", then "visit this website". In Chrome on Android or a laptop, tap "Advanced", then "Proceed to" the address. Firefox has "Advanced", then "Accept the Risk and Continue".

An install from before Caddy keeps its old certificate in the `tls` folder of the data volume, and Caddy serves it as it is, so phones that accepted it keep it (D119). [HTTPS after an upgrade from an older image](install-docker.md#https-after-an-upgrade-from-an-older-image) says how to move to the authority instead.

## Use a certificate of your own

A server reached by a real host name, such as `derby.example.org`, can have a certificate that browsers trust, so nobody installs anything. Let's Encrypt (<https://letsencrypt.org/>) gives them at no charge.

- Put a `DERBYNET_DOMAIN=derby.example.org` line in `.env`, and Caddy gets one from Let's Encrypt. See [HTTPS and the certificate](install-docker.md#https-and-the-certificate).
- Or run DerbyNet behind your own web server or proxy, which holds the certificate. See [Use your own proxy](install-docker.md#use-your-own-proxy).

## Take photos without HTTPS

Check-in works over plain HTTP. "Take racer photo" and "Take car photo" open the phone's camera app, or let you pick a photo from the phone or the computer. Only the live camera inside the page needs HTTPS.
