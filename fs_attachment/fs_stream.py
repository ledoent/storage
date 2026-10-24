# Copyright 2023 ACSONE SA/NV
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from __future__ import annotations

import typing

from werkzeug.utils import send_file as _send_file

from odoo.http import Response, request
from odoo.http.stream import STATIC_CACHE_LONG, Stream

if typing.TYPE_CHECKING:
    from .models.ir_attachment import IrAttachment


class FsStream(Stream):
    """Stream for the attachments stored into a filesystem storage.

    The content is streamed from the filesystem storage (or served by the
    proxy using X-Sendfile / X-Accel-Redirect) without being loaded into memory.

    If the content is accessed through the ``data`` attribute (for example
    by ``ir.binary._get_image_stream_from`` to resize an image), it's loaded
    into memory and the stream becomes a standard 'data' stream.
    """

    type: typing.Literal["data", "path", "url", "fs"]
    fs_attachment: IrAttachment | None = None
    # Resolved in ``from_fs_attachment`` so that serving the response needs no
    # database access. See the comment there.
    fs_filesystem: typing.Any = None
    fs_path: str | None = None
    fs_use_x_sendfile: bool = False
    fs_x_sendfile_path: str | None = None
    _data: bytes | None = None

    def __init__(self, **kwargs):
        # The base constructor only supports 'data', 'path' and 'url' types.
        # Remove class methods from the instances (as done in the base
        # constructor)
        self.from_path = None
        self.__dict__.update(kwargs)
        assert self.type == "fs", f"Invalid type {self.type!r} in FsStream"
        assert self.fs_attachment, "Missing attribute 'fs_attachment' to FsStream"

    @classmethod
    def from_fs_attachment(cls, attachment: IrAttachment) -> FsStream:
        attachment.ensure_one()
        if not attachment.fs_filename:
            raise ValueError("Attachment is not stored into a filesystem storage")
        # A stream has to be self-contained once built: the caller may serve it
        # after the cursor it was built in has closed. ``web``'s
        # ``content_assets`` does exactly that -- it opens a read-write cursor
        # to generate a missing asset bundle and calls ``get_response()`` after
        # that ``with`` block exits -- so the first request for every bundle
        # after an upgrade used to 500 with "Cursor already closed".
        #
        # Caching the fields is not enough, because closing the cursor also
        # clears the environment cache. They are read as plain values here. The
        # fsspec filesystem is not bound to the cursor and opens nothing until
        # it is read, so holding it costs no I/O for a request that 304s.
        use_x_sendfile = attachment._fs_use_x_sendfile()
        return cls(
            fs_filesystem=attachment.fs_storage_id.fs,
            fs_path=attachment.fs_filename,
            fs_use_x_sendfile=use_x_sendfile,
            fs_x_sendfile_path=(
                attachment._get_x_sendfile_path() if use_x_sendfile else None
            ),
            mimetype=attachment.mimetype,
            download_name=attachment.name,
            conditional=True,
            etag=attachment.checksum,
            type="fs",
            size=attachment.file_size,
            last_modified=attachment["write_date"],
            public=attachment.public,
            fs_attachment=attachment,
        )

    @property
    def data(self) -> bytes | None:
        if self._data is None and self.type == "fs":
            self.data = self.read()
        return self._data

    @data.setter
    def data(self, value: bytes | None) -> None:
        self._data = value
        if value is not None:
            # from now, the content is served from memory
            self.type = "data"

    def read(self) -> bytes:
        if self.type == "fs":
            with self.fs_filesystem.open(self.fs_path, "rb") as f:
                return f.read()
        return super().read()

    def get_response(
        self,
        as_attachment=None,
        immutable=None,
        content_security_policy="default-src 'none'",
        **send_file_kwargs,
    ):
        if self.type != "fs":
            return super().get_response(
                as_attachment=as_attachment,
                immutable=immutable,
                content_security_policy=content_security_policy,
                **send_file_kwargs,
            )
        if as_attachment is None:
            as_attachment = self.as_attachment
        if immutable is None:
            immutable = self.immutable
        send_file_kwargs = {
            "mimetype": self.mimetype,
            "as_attachment": as_attachment,
            "download_name": self.download_name,
            "conditional": self.conditional,
            "etag": self.etag,
            "last_modified": self.last_modified,
            "max_age": STATIC_CACHE_LONG if immutable else self.max_age,
            "environ": request.httprequest.environ,
            "response_class": Response,
            **send_file_kwargs,
        }
        use_x_sendfile = self.fs_use_x_sendfile
        send_file_kwargs["use_x_sendfile"] = use_x_sendfile
        if not use_x_sendfile:
            # The file will be closed by werkzeug...
            f = self.fs_filesystem.open(self.fs_path, "rb")
            res = _send_file(f, **send_file_kwargs)
        else:
            x_sendfile_path = self.fs_x_sendfile_path
            res = _send_file("", **send_file_kwargs)
            # nginx specific headers
            res.headers["X-Accel-Redirect"] = x_sendfile_path
            # apache specific headers
            res.headers["X-Sendfile"] = x_sendfile_path
            res.headers["Content-Length"] = 0

        res.headers["X-Content-Type-Options"] = "nosniff"

        if content_security_policy:
            res.headers["Content-Security-Policy"] = content_security_policy

        if self.public:
            if (res.cache_control.max_age or 0) > 0:
                res.cache_control.public = True
        else:
            res.cache_control.pop("public", "")
            res.cache_control.private = True
        if immutable:
            res.cache_control["immutable"] = None

        return res
