# Copyright 2026 Ledo Enterprises
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Fs Attachment GCS",
    "summary": """Store attachments into Google Cloud Storage""",
    "version": "20.0.1.0.0",
    "license": "AGPL-3",
    "author": "Ledo Enterprises,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/storage",
    "depends": ["fs_attachment"],
    "external_dependencies": {
        "python": [
            "gcsfs",
        ],
    },
    "data": [
        "views/fs_storage.xml",
    ],
}
