"""Open an original without following symlinks in any path component."""

import os
import stat
from pathlib import Path


def open_original(path: str, root: str):
    target, source = Path(path), Path(root)
    if (
        not target.is_absolute()
        or not source.is_absolute()
        or ".." in target.parts
        or not target.is_relative_to(source)
    ):
        raise OSError("File is outside its registered source.")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in target.parts[1:-1]:
            next_descriptor = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = next_descriptor
        file_descriptor = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=descriptor)
        try:
            if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
                raise OSError("Not a regular file.")
            return os.fdopen(file_descriptor, "rb")
        except Exception:
            os.close(file_descriptor)
            raise
    finally:
        os.close(descriptor)
