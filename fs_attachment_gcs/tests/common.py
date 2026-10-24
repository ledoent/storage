# Copyright 2026 Ledo Enterprises
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from odoo.tests.common import TransactionCase


class TestFSAttachmentGcsCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))
        cls.gcs_backend = cls.env["fs.storage"].create(
            {
                "name": "GCS Storage",
                "protocol": "gs",
                "code": "gcstst",
                "directory_path": "test-bucket",
                # `token: anon` keeps gcsfs from reaching for credentials at
                # instantiation. No request is made by these tests -- the
                # stored path is faked below -- but building the filesystem
                # object must not require a metadata server.
                "json_options": {"token": "anon"},
                # GCS serves from one fixed host, so base_url plays no part in
                # x-sendfile here, exactly as for S3.
                "base_url": False,
            }
        )
        cls.fake_attachment_gcs = cls.env["ir.attachment"].create(
            {
                "name": "fake_gcs_file.txt",
                "fs_storage_id": cls.gcs_backend.id,
            }
        )
        cls.fake_attachment_gcs.flush_recordset()
        # Written straight to the row, as fs_attachment_s3's fixture does:
        # there is no bucket behind this and the point of the test is the path
        # arithmetic, not a round-trip.
        cls.env.cr.execute(
            """
                UPDATE ir_attachment
                   SET store_fname = 'gcstst://dir/sub/fake_gcs_file.txt',
                       fs_filename = 'fake_gcs_file.txt',
                       fs_storage_code = 'gcstst',
                       checksum = 234,
                       file_size = 1234,
                       fs_storage_id = %s
                 WHERE id = %s
            """,
            (cls.gcs_backend.id, cls.fake_attachment_gcs.id),
        )
        cls.fake_attachment_gcs.invalidate_recordset()
