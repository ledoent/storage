Go to *Settings* > *Technical* > *Storage* > *File Storage*.

When a storage uses protocol `gs` (or `gcs`) and you enable **Use X-Sendfile
To Serve Internal Url**, two fields appear:

- **Use signed URL for X-Accel-Redirect** — generate a short-lived V4 signed
  URL for each request.
- **Signed URL Expiration (seconds)** — default 30. It can be low: the URL is
  consumed immediately by the web server, not handed to the browser.

## Signing without a private key

A V4 signature needs something that can sign bytes. There are two ways to get
one, and this module prefers the second:

1. a **service-account private key** in the credentials — simple, and a
   long-lived secret that has to exist somewhere;
2. **IAM SignBlob delegation** — the running identity asks Google to sign on
   behalf of a service account it may impersonate. No key material anywhere.

`gcsfs.sign()` alone only does (1): it passes the credentials to
`generate_signed_url`, which under Workload Identity raises

```text
AttributeError: you need a private key to sign credentials
```

This module detects credentials that cannot sign locally and supplies
`service_account_email` + `access_token`, which is route (2). **So an instance
on Workload Identity can serve signed URLs with no service-account key in
existence** — which is what makes this usable under an
`iam.disableServiceAccountKeyCreation` org policy.

It requires the workload's identity to hold **`iam.serviceAccounts.signBlob`**
on the service account it runs as — `roles/iam.serviceAccountTokenCreator` on
itself. Without it, signing fails with a permission error **when a file is
served, not when the option is ticked.**

Credentials that *can* sign locally are left alone: adding the delegation
kwargs would force an IAM round trip per URL for no benefit.

Signatures are **v4**. `google-cloud-storage` still defaults to the deprecated
v2; this module passes `version="v4"` explicitly so URLs do not stop working
on a date nobody chose.

## Leaving signing off

The unsigned form is `https://storage.googleapis.com/<bucket>/<key>`, which
the web server can fetch only if the object is publicly readable. On a private
bucket the redirect will 403 with nothing wrong in the Odoo log. Leave signing
off only for a deliberately public bucket or one fronted by Cloud CDN.
