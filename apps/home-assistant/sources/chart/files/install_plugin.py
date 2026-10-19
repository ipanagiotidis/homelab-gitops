"""Install the reviewed plugin without network access or runtime package installs."""
import hashlib
import importlib.metadata
from pathlib import Path
import stat
import sys
import zipfile

EXPECTED = "e5badaaacaa63cfd6fe733924a05e76d75058836190398598fb24de57cd47ccd"
archive = Path(sys.argv[1])
target = Path(sys.argv[2]).resolve()
if hashlib.sha256(archive.read_bytes()).hexdigest() != EXPECTED:
    raise SystemExit("SSO archive checksum mismatch")
# Fail closed if a future HA image drops a dependency; never install from PyPI.
for package in ("aiofiles", "jinja2", "joserfc", "psycopg2"):
    importlib.metadata.version(package)
target.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(archive) as bundle:
    for entry in bundle.infolist():
        path = (target / entry.filename).resolve()
        if not path.is_relative_to(target) or stat.S_ISLNK(entry.external_attr >> 16):
            raise SystemExit("Unsafe path in SSO archive")
    bundle.extractall(target)
print("Pinned SSO integration verified and installed")
