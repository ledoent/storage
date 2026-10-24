# Copyright 2026 Ledo Enterprises
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models

# gcsfs advertises both spellings; a storage configured with either protocol is
# the same filesystem and must be treated identically.
GCS_PROTOCOLS = ("gs", "gcs")

# GCS serves every object from one host. Unlike S3 there is no per-endpoint
# URL to read off the client, so the unsigned form is built from this.
GCS_PUBLIC_HOST = "https://storage.googleapis.com"


class FsStorage(models.Model):
    _inherit = "fs.storage"

    gcs_uses_signed_url_for_x_sendfile = fields.Boolean(
        string="Use signed URL for X-Accel-Redirect",
        help="If checked, the storage will use signed URLs for attachments "
        "when using X-Accel-Redirect. Required unless the bucket is public: "
        "a GCS object path is not readable without authentication.",
    )
    gcs_signed_url_expiration = fields.Integer(
        string="Signed URL Expiration (seconds)",
        default=30,
        help="The expiration time for the signed URL in seconds. "
        "Default is 30 seconds.",
    )

    @property
    def is_gcs_storage(self):
        """Check if the storage is a GCS storage.

        Detected by PROTOCOL rather than by probing for a client attribute.
        fs_attachment_s3 uses ``hasattr(root_fs, "s3")`` because s3fs exposes
        its aiobotocore client under that name; gcsfs has no single equivalent
        attribute, and picking one would couple this module to a gcsfs
        internal. ``protocol`` is part of the fsspec public interface and is
        declared by every filesystem.
        """
        self.ensure_one()
        protocol = getattr(self._get_root_filesystem(self.fs), "protocol", ())
        if isinstance(protocol, str):
            protocol = (protocol,)
        return any(p in GCS_PROTOCOLS for p in protocol)

    @api.model
    def _gcs_generate_signed_url(self, root_fs, path, expiration):
        """Return a V4 signed URL for ``path`` (``bucket/key``).

        SIGNING NEEDS A SIGNER, which is the operational trap worth stating
        here rather than in a traceback. ``GCSFileSystem.sign`` ultimately
        needs either a service-account private key in the credentials, or the
        ``iam.serviceAccounts.signBlob`` permission so Google can sign on the
        caller's behalf. A storage configured with ``{"token": "cloud"}`` --
        Workload Identity, which is the keyless setup most deployments should
        prefer -- has NO private key, so signing fails until the running
        identity is granted ``roles/iam.serviceAccountTokenCreator`` on
        itself.

        The failure surfaces only when a file is actually served, not when the
        option is ticked, so it is easy to enable and discover later.
        """
        return root_fs.sign(path, expiration=expiration)
