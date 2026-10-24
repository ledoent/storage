Go to *Settings* > *Technical* > *Storage* > *File Storage*.

When a storage uses protocol `gs` (or `gcs`) and you enable **Use X-Sendfile
To Serve Internal Url**, two fields appear:

- **Use signed URL for X-Accel-Redirect** — generate a short-lived V4 signed
  URL for each request.
- **Signed URL Expiration (seconds)** — default 30. It can be low: the URL is
  consumed immediately by the web server, not handed to the browser.

## Signing needs a signer

`gcsfs` can only sign when the credentials can sign. Either:

- the credentials contain a **service-account private key**, or
- the running identity holds **`iam.serviceAccounts.signBlob`** on the service
  account it acts as, i.e. `roles/iam.serviceAccountTokenCreator` on itself.

A storage configured with `{"token": "cloud"}` — Workload Identity, the
keyless setup most deployments should prefer — has no private key, so signing
fails until that role is granted. **The failure appears only when a file is
served, not when the option is ticked.**

## Leaving signing off

The unsigned form is `https://storage.googleapis.com/<bucket>/<key>`, which
the web server can fetch only if the object is publicly readable. On a private
bucket the redirect will 403 with nothing wrong in the Odoo log. Leave signing
off only for a deliberately public bucket or one fronted by Cloud CDN.
