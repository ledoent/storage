# Copyright 2026 Ledo Enterprises
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

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
    def _gcs_signing_kwargs(self, credentials):
        """Extra ``generate_signed_url`` kwargs so signing needs NO private key.

        THIS IS THE POINT OF THE MODULE, not a detail. A V4 signature needs
        something that can sign bytes, and there are only two ways to get one:

        1. **A service-account private key** in the credentials. Simple, and a
           long-lived secret on disk or in a database field -- the thing an
           organisation enabling ``iam.disableServiceAccountKeyCreation``
           has decided it does not want to exist.
        2. **IAM SignBlob delegation.** The running identity asks Google to
           sign on behalf of a service account it is allowed to impersonate.
           No key material anywhere, short-lived tokens only.

        ``gcsfs.sign()`` does neither by itself: it hands the credentials to
        ``blob.generate_signed_url()``, which can only take route 1. Under
        Workload Identity -- the keyless setup a GKE or Cloud Run deployment
        should prefer -- those credentials carry no key, and the call raises::

            AttributeError: you need a private key to sign credentials

        ``generate_signed_url`` does support route 2, through
        ``service_account_email`` + ``access_token``, but only if the caller
        supplies them. This does, for exactly the credentials that cannot sign
        locally, which is what lets an instance serve signed URLs with no
        service-account key in existence.

        A credential that CAN sign locally is left alone: adding the kwargs
        would force an unnecessary IAM round trip per URL, and would fail on
        an identity without ``iam.serviceAccounts.signBlob`` on itself.
        """
        # Imported lazily: the module must remain importable where
        # google-auth is absent, e.g. a registry build that never touches GCS.
        import google.auth.credentials
        import google.auth.transport.requests

        if isinstance(credentials, google.auth.credentials.Signing):
            # Has a private key (or an equivalent local signer). Nothing to add.
            return {}

        email = getattr(credentials, "service_account_email", None)
        if not credentials.valid or not email:
            # service_account_email is only populated once the metadata server
            # has been asked, so a refresh is required before reading it --
            # not merely to obtain a token.
            credentials.refresh(google.auth.transport.requests.Request())
            email = getattr(credentials, "service_account_email", None)

        if not email or email == "default":
            # "default" is what the metadata server returns when the instance
            # has no specific service account bound. Signing cannot work, and
            # guessing an address would produce a signature attributed to the
            # wrong principal.
            raise UserError(
                self.env._(
                    "Cannot sign GCS URLs: the credentials have no private key "
                    "and no resolvable service account to delegate to. Bind a "
                    "service account to this workload, or configure the storage "
                    "with service-account credentials."
                )
            )

        _logger.debug("GCS signing via IAM SignBlob delegation as %s", email)
        return {"service_account_email": email, "access_token": credentials.token}

    @api.model
    def _gcs_generate_signed_url(self, root_fs, path, expiration):
        """Return a V4 signed URL for ``path`` (``bucket/key``).

        ``version="v4"`` is explicit. google-cloud-storage still defaults to
        v2, which is deprecated and which GCS has signalled it will stop
        honouring; leaving the default would produce URLs that work now and
        stop working on a date nobody here chose.
        """
        signing_kwargs = self._gcs_signing_kwargs(root_fs.credentials.credentials)
        return root_fs.sign(path, expiration=expiration, version="v4", **signing_kwargs)
