# Copyright 2026 Ledo Enterprises
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from unittest.mock import patch

from .common import TestFSAttachmentGcsCommon


class TestFSAttachmentGcs(TestFSAttachmentGcsCommon):
    def test_is_gcs_storage_detects_by_protocol(self):
        """Detection must not depend on a gcsfs internal attribute.

        fs_attachment_s3 probes `hasattr(root_fs, "s3")`. gcsfs exposes no
        single equivalent, so this module reads `protocol`, which is part of
        the fsspec public interface.
        """
        self.assertTrue(self.gcs_backend.is_gcs_storage)

    def test_a_non_gcs_storage_is_not_claimed(self):
        """The guard has to be negative for everything else, or this module
        would hijack the x-sendfile path of every other backend."""
        other = self.env["fs.storage"].create(
            {
                "name": "Memory Storage",
                "protocol": "memory",
                "code": "memtst",
                "directory_path": "/tmp",
            }
        )
        self.assertFalse(other.is_gcs_storage)

    def test_unsigned_x_sendfile_path(self):
        """GCS has one host, so the unsigned form is fixed."""
        url = self.fake_attachment_gcs._get_x_sendfile_path()
        self.assertEqual(
            url,
            "/fs_x_sendfile/https/storage.googleapis.com"
            "/test-bucket/dir/sub/fake_gcs_file.txt",
            f"unexpected X-Accel-Redirect path ({url})",
        )

    def test_signed_x_sendfile_path_keeps_the_query(self):
        """A V4 signed URL carries its signature in the query string, so the
        redirect path must preserve it. Dropping the query is the mistake
        that yields a 403 the Odoo log knows nothing about.

        `sign` is patched rather than called: signing needs a private key or
        `iam.serviceAccounts.signBlob`, neither of which exists in CI.
        """
        self.gcs_backend.write(
            {
                "gcs_uses_signed_url_for_x_sendfile": True,
                "gcs_signed_url_expiration": 60,
            }
        )
        signed = (
            "https://storage.googleapis.com/test-bucket/dir/sub/fake_gcs_file.txt"
            "?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Expires=60"
        )
        with patch.object(
            type(self.env["fs.storage"]),
            "_gcs_generate_signed_url",
            return_value=signed,
        ) as mocked:
            url = self.fake_attachment_gcs._get_x_sendfile_path()
        self.assertTrue(mocked.called, "signed mode did not call the signer")
        self.assertEqual(
            url,
            "/fs_x_sendfile/https/storage.googleapis.com"
            "/test-bucket/dir/sub/fake_gcs_file.txt"
            "?X-Goog-Algorithm=GOOG4-RSA-SHA256&X-Goog-Expires=60",
        )

    def test_the_configured_expiration_is_passed_to_the_signer(self):
        """A default-valued argument silently ignored is the classic way an
        expiration setting does nothing."""
        self.gcs_backend.write(
            {
                "gcs_uses_signed_url_for_x_sendfile": True,
                "gcs_signed_url_expiration": 77,
            }
        )
        with patch.object(
            type(self.env["fs.storage"]),
            "_gcs_generate_signed_url",
            return_value="https://storage.googleapis.com/test-bucket/x",
        ) as mocked:
            self.fake_attachment_gcs._get_x_sendfile_path()
        self.assertEqual(mocked.call_args.args[-1], 77)

    def test_write_option_carries_the_mimetype_snake_cased(self):
        """gcsfs names the keyword `content_type`; S3 names it `ContentType`.

        The option dict is splatted into `fs.open(path, "wb", **kwargs)`, so
        the S3 spelling would be passed to GCSFile as an unexpected keyword.
        """
        attachment = self.env["ir.attachment"].with_context(mimetype="image/png")
        option = attachment._storage_write_option(self.gcs_backend.fs)
        self.assertEqual(option.get("content_type"), "image/png")
        self.assertNotIn("ContentType", option)

    def test_write_option_is_untouched_without_a_mimetype(self):
        option = self.env["ir.attachment"]._storage_write_option(self.gcs_backend.fs)
        self.assertNotIn("content_type", option)
