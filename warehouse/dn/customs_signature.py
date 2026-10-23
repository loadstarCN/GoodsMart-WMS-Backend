"""Load a company-authorized CI signature from a private server file.

The image is runtime data. It must never be stored in the source repository.
"""
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from flask import current_app
from PIL import Image, UnidentifiedImageError


MAX_SIGNATURE_BYTES = 2 * 1024 * 1024
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class SignatureUnavailable(Exception):
    """A configured signature cannot safely be used for a CI."""


@dataclass(frozen=True)
class SignatureImage:
    content: bytes
    sha256: str
    width: int
    height: int


@dataclass(frozen=True)
class SignaturePolicy:
    path: str
    signatory_name: str
    signatory_title: str


def company_signature_policy(company_id: int, dn_id: int) -> SignaturePolicy | None:
    """Scope the private signature and its signer to future DNs of one company."""
    configured_id = str(current_app.config.get('CUSTOMS_SIGNATURE_COMPANY_ID') or '').strip()
    configured_path = str(current_app.config.get('CUSTOMS_SIGNATURE_PATH') or '').strip()
    from_dn_id = str(current_app.config.get('CUSTOMS_SIGNATURE_FROM_DN_ID') or '').strip()
    name = str(current_app.config.get('CUSTOMS_SIGNATURE_SIGNATORY_NAME') or '').strip()
    title = str(current_app.config.get('CUSTOMS_SIGNATURE_SIGNATORY_TITLE') or '').strip()
    if not any((configured_id, configured_path, from_dn_id, name, title)):
        return None
    if not all((configured_id, configured_path, from_dn_id, name, title)) or not (
        configured_id.isdigit() and from_dn_id.isdigit() and int(from_dn_id) > 0
    ):
        raise SignatureUnavailable('The exporter signature configuration is incomplete')
    if int(configured_id) != company_id or dn_id < int(from_dn_id):
        return None
    return SignaturePolicy(configured_path, name, title)


def load_company_signature(company_id: int, dn_id: int) -> SignatureImage | None:
    """Return the applicable company's PNG, or None for DNs before activation."""
    policy = company_signature_policy(company_id, dn_id)
    if policy is None:
        return None

    path = Path(policy.path)
    if not path.is_absolute() or path.resolve().is_relative_to(REPOSITORY_ROOT):
        raise SignatureUnavailable('The exporter signature must be stored outside the source repository')
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise SignatureUnavailable('The configured exporter signature cannot be read') from exc
    if not content or len(content) > MAX_SIGNATURE_BYTES:
        raise SignatureUnavailable('The configured exporter signature has an invalid file size')
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format != 'PNG' or not (0 < image.width <= 4000 and 0 < image.height <= 2000):
                raise SignatureUnavailable('The exporter signature must be a valid PNG image')
            width, height = image.size
            image.verify()
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise SignatureUnavailable('The exporter signature must be a valid PNG image') from exc
    return SignatureImage(content, hashlib.sha256(content).hexdigest(), width, height)
