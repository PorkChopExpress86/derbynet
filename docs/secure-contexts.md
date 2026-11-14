# Secure contexts and DerbyNet

Browsers give a web page the camera and the serial port only when the page is secure. The page must come over HTTPS, or from the same computer as `localhost`. The web standard calls such a page a secure context.

On a race's own network, DerbyNet serves both plain HTTP and HTTPS. This guide says which pages need HTTPS, and how each phone accepts the server's certificate.

## Which pages need HTTPS

| Page | Needs | Over plain HTTP |
|---|---|---|
| The browser timer (`/timer.php`) | Web Serial, to talk to the timer | The page sends you to a page that says to use Chrome or Edge. |
| Replay (`/replay/`) | The camera | The browser offers no camera. |
| The replay camera (`/replay/camera/`) | The camera | The browser offers no camera. |
| Check-in and the photo pages, "Use the live camera" | The camera | The button is hidden. "Take racer photo" and "Take car photo" still work: they open the phone's own camera app. |

Every other page works over plain HTTP: check-in without the live camera, the race dashboard, the kiosks, standings, and these guides. Kiosk screens, `derby-timer.jar`, and the Raspberry Pi scripts use plain HTTP, and the server never redirects them to HTTPS.

On the computer that runs the server, `http://localhost/` is already secure. Every other device uses the HTTPS address, such as `https://192.168.1.20/`.

## The certificate of this install

HTTPS needs a certificate, which tells the browser who the server is. On its first start, the DerbyNet container makes a certificate and a private key for this install, and keeps them in the `tls` folder of the data volume. Every install has its own key, and nobody else has a copy. The certificate lasts 825 days, and the container makes a new one when it has expired.

The certificate is self-signed: no certificate authority vouches for it, so each browser warns the first time it opens the HTTPS address. The warning is expected for this server on your own network. Accept it once on each device, and the browser remembers it.

A browser warns again when the certificate changes: after it expires, or after you start a new data volume.

### Accept it on an iPhone or iPad

1. Open the HTTPS address in Safari, such as `https://192.168.1.20/`.
2. Safari says "This Connection Is Not Private". Tap "Show Details".
3. Tap "visit this website", then "Visit Website" to confirm.

### Accept it on an Android phone

1. Open the HTTPS address in Chrome.
2. Chrome says "Your connection is not private". Tap "Advanced".
3. Tap "Proceed to" the address.

### Accept it on a laptop

In Chrome or Edge, the warning page has an "Advanced" button, then a link to go on to the address. Firefox has "Advanced", then "Accept the Risk and Continue". On a Mac, Safari works as on an iPhone.

## Use a certificate of your own

A server reached by a real host name, such as `derby.example.org`, can have a certificate that browsers trust, so nobody sees a warning. Let's Encrypt (<https://letsencrypt.org/>) gives them at no charge.

- Put the certificate as `cert.pem`, and its private key as `key.pem`, in the `tls` folder of the data volume, before you start the container. The container then uses them, and makes none of its own.
- Or run DerbyNet behind your web site's own web server, which holds the certificate. See [Host DerbyNet on a web server](install-docker.md#host-derbynet-on-a-web-server).

## Take photos without HTTPS

Check-in works over plain HTTP. "Take racer photo" and "Take car photo" open the phone's camera app, or let you pick a photo from the phone or the computer. Only the live camera inside the page needs HTTPS.
