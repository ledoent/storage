This module adds Google Cloud Storage specific behaviour on top of
`fs_attachment`.

Basic GCS storage needs no module at all: `fs_storage` exposes every protocol
`fsspec` can see, so a storage with protocol `gs` and `gcsfs` installed already
stores attachments in a bucket. This module adds the two things that protocol
cannot infer:

- the object's `contentType`, so a file served straight from the bucket
  arrives with the right header instead of `application/octet-stream`;
- `X-Accel-Redirect` support, with optional V4 signed URLs, so the web server
  serves the bytes instead of Odoo.

It is the GCS counterpart of `fs_attachment_s3` and emits the same
`/fs_x_sendfile/<scheme>/<host>/<path>` shape, so one nginx `internal`
location serves both.
