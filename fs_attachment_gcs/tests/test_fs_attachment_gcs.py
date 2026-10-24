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


class TestGcsKeylessSigning(TestFSAttachmentGcsCommon):
    """Signing without a service-account private key.

    The reason the module exists in this shape: an organisation running
    `iam.disableServiceAccountKeyCreation` has no key to sign with, and
    `gcsfs.sign()` alone cannot sign without one.
    """

    def test_credentials_that_can_sign_locally_are_left_alone(self):
        """No IAM round trip when a private key is already present.

        Adding the delegation kwargs unconditionally would cost a SignBlob
        call per URL and would fail outright on an identity that lacks
        `iam.serviceAccounts.signBlob` on itself.
        """
        import google.auth.credentials

        class LocalSigner(google.auth.credentials.Signing):
            def sign_bytes(self, message):  # pragma: no cover - never called
                return b""

            @property
            def signer_email(self):
                return "key@example.iam.gserviceaccount.com"

            @property
            def signer(self):  # pragma: no cover - never called
                return None

        kwargs = self.env["fs.storage"]._gcs_signing_kwargs(LocalSigner())
        self.assertEqual(kwargs, {})

    def test_keyless_credentials_delegate_to_iam_signblob(self):
        """The whole point: a token and an email instead of a private key."""

        class MetadataCreds:
            """Shaped like compute_engine.Credentials: no Signing base."""

            valid = True
            token = "ya29.fake-access-token"
            service_account_email = "runtime@proj.iam.gserviceaccount.com"

            def refresh(self, request):  # pragma: no cover - already valid
                raise AssertionError("should not refresh when already valid")

        kwargs = self.env["fs.storage"]._gcs_signing_kwargs(MetadataCreds())
        self.assertEqual(
            kwargs,
            {
                "service_account_email": "runtime@proj.iam.gserviceaccount.com",
                "access_token": "ya29.fake-access-token",
            },
        )

    def test_an_unresolved_service_account_is_refused_not_guessed(self):
        """The metadata server answers "default" when no service account is
        bound. Signing as a guessed principal is worse than failing."""
        from odoo.exceptions import UserError

        class Unbound:
            valid = True
            token = "tok"
            service_account_email = "default"

            def refresh(self, request):
                pass

        with self.assertRaises(UserError):
            self.env["fs.storage"]._gcs_signing_kwargs(Unbound())

    def test_credentials_are_refreshed_to_resolve_the_account(self):
        """`service_account_email` is only populated once the metadata server
        has been asked, so the refresh is about the EMAIL, not just the token."""
        state = {"refreshed": False}

        class Lazy:
            valid = False
            token = None
            service_account_email = None

            def refresh(inner, request):
                state["refreshed"] = True
                inner.valid = True
                inner.token = "tok"
                inner.service_account_email = "late@proj.iam.gserviceaccount.com"

        kwargs = self.env["fs.storage"]._gcs_signing_kwargs(Lazy())
        self.assertTrue(state["refreshed"])
        self.assertEqual(
            kwargs["service_account_email"], "late@proj.iam.gserviceaccount.com"
        )

    def test_signatures_are_v4(self):
        """google-cloud-storage still defaults to the deprecated v2."""
        captured = {}

        class FakeFs:
            class credentials:
                credentials = None

            def sign(self, path, **kwargs):
                captured.update(kwargs)
                return "https://storage.googleapis.com/b/k?sig=1"

        fs = FakeFs()
        fs.credentials.credentials = type(
            "C", (), {"valid": True, "token": "t", "service_account_email": "a@b.com"}
        )()
        self.env["fs.storage"]._gcs_generate_signed_url(fs, "bucket/key", 30)
        self.assertEqual(captured.get("version"), "v4")
        self.assertEqual(captured.get("expiration"), 30)
