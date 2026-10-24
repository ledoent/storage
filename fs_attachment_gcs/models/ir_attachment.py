# Copyright 2026 Ledo Enterprises
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from urllib.parse import urlparse

from odoo import models

from .fs_storage import GCS_PROTOCOLS, GCS_PUBLIC_HOST


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def _storage_write_option(self, fs):
        """Carry the mimetype onto the stored object.

        GCS stores a per-object ``contentType`` and serves it back as the
        response ``Content-Type``. Without this every object is written as
        ``application/octet-stream``, which is invisible while Odoo serves the
        file itself -- Odoo sets the header from the attachment record -- and
        becomes visible the moment X-Accel-Redirect or a public URL hands the
        object to a client directly, at which point an image downloads instead
        of rendering.

        ``content_type`` lowercase, not S3's ``ContentType``: the option dict
        is splatted into ``fs.open(path, "wb", **kwargs)``, and gcsfs names the
        keyword on ``GCSFile.__init__`` in snake_case.
        """
        option = super()._storage_write_option(fs)
        mimetype = self.env.context.get("mimetype")
        if mimetype:
            root_fs = self.env["fs.storage"]._get_root_filesystem(fs)
            protocol = getattr(root_fs, "protocol", ())
            if isinstance(protocol, str):
                protocol = (protocol,)
            if any(p in GCS_PROTOCOLS for p in protocol):
                option["content_type"] = mimetype
        return option

    def _get_x_sendfile_path(self):
        self.ensure_one()
        if self.fs_storage_id.is_gcs_storage:
            return self._get_gcs_x_sendfile_path()
        return super()._get_x_sendfile_path()

    def _fs_use_x_sendfile(self):
        self.ensure_one()
        storage = self.fs_storage_id
        if storage.is_gcs_storage:
            return storage.use_x_sendfile_to_serve_internal_url
        return super()._fs_use_x_sendfile()

    def _get_gcs_x_sendfile_path(self):
        """Generate the X-Accel-Redirect path for GCS storage.

        The path is formatted as::

            /fs_x_sendfile/<scheme>/<netloc>/<path>[?<query>]

        matching fs_attachment_s3, so one nginx ``internal`` location serves
        both backends.

        TWO MODES, AND THE UNSIGNED ONE IS RARELY WHAT YOU WANT. Signed
        produces a short-lived V4 URL and works on a private bucket. Unsigned
        builds ``https://storage.googleapis.com/<bucket>/<key>``, which the web
        server can only fetch if the object is publicly readable -- so leaving
        the option off on a private bucket yields a redirect that 403s, with
        nothing wrong in the Odoo log. It is still offered because a bucket
        fronted by Cloud CDN, or one deliberately public, has no reason to pay
        for signing on every request.
        """
        self.ensure_one()
        fs, storage_code, file_path = self._get_fs_parts()
        storage = self.env["fs.storage"].sudo().get_by_code(storage_code)
        root_fs = storage._get_root_filesystem(fs)
        bucket_name = storage.get_directory_path().strip("/")
        object_path = f"{bucket_name}/{file_path.lstrip('/')}"

        if storage.gcs_uses_signed_url_for_x_sendfile:
            file_url = storage._gcs_generate_signed_url(
                root_fs, object_path, storage.gcs_signed_url_expiration
            )
        else:
            file_url = f"{GCS_PUBLIC_HOST}/{object_path}"

        parsed_url = urlparse(file_url)
        path = parsed_url.path.strip("/")
        redirect_path = f"/fs_x_sendfile/{parsed_url.scheme}/{parsed_url.netloc}/{path}"
        if parsed_url.query:
            redirect_path += f"?{parsed_url.query}"
        return redirect_path
