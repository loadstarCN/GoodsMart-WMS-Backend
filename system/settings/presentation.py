"""Public presentation settings; no SMTP or access-control secrets are exposed."""
import json
import re
from datetime import date, datetime
from urllib.parse import urlsplit
from sqlalchemy.exc import IntegrityError
from extensions.db import db
from extensions.error import BadRequestException, ConflictException
from warehouse.common import clock
from .models import SystemSetting

KEY = 'platform_presentation'
DEFAULT = dict(revision=0, brand={}, pda={}, notices=[])
BRAND = ('productName', 'logoOnDark', 'logoOnLight', 'logoMark', 'favicon', 'loginTitle', 'loginSubtitle', 'loginImage')
PDA = ('url', 'version', 'versionCode', 'minimumVersion', 'releaseDate', 'releasedAt', 'sizeBytes', 'sha256', 'login', 'header')


def upload_image():
    from io import BytesIO
    from flask import request
    from PIL import Image, UnidentifiedImageError
    from werkzeug.datastructures import FileStorage
    from extensions.oss import oss
    source = request.files.get('file')
    if source is None:
        raise BadRequestException('An image file is required.', 14017)
    raw = source.read(5 * 1024 * 1024 + 1)
    if len(raw) > 5 * 1024 * 1024:
        raise BadRequestException('Image must be at most 5 MB.', 14017)
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format not in ('PNG', 'JPEG', 'GIF') or image.width * image.height > 16_000_000:
                raise ValueError('Unsupported image')
            # Decode and re-encode pixels: no original metadata or active content is published.
            image.load()
            encoded = BytesIO()
            image.convert('RGBA').save(encoded, format='PNG')
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise BadRequestException('Use a valid PNG, JPEG or GIF image up to 16 megapixels.', 14017)
    encoded.seek(0)
    return {'file_url': oss.upload_file(FileStorage(encoded, filename='brand.png', content_type='image/png'), 'platform/brand/')}


def read():
    row = db.session.get(SystemSetting, KEY)
    return json.loads(row.value) if row else dict(DEFAULT)


def public():
    data = read()
    now = clock.now()
    data['notices'] = [row for row in data['notices'] if row['enabled']
        and (not row['start'] or clock.parse_input_datetime(row['start'], None) <= now)
        and (not row['end'] or now < clock.parse_input_datetime(row['end'], None))]
    return data


def _text(value, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise BadRequestException('Invalid presentation setting text.', 14017)
    return value.strip()


def _url(value):
    value = _text(value, 2048)
    if not value:
        return value
    parsed = urlsplit(value)
    if '\\' in value or any(ord(c) < 32 for c in value) or not (
        (value.startswith('/') and not value.startswith('//')) or
        (parsed.scheme in ('http', 'https') and parsed.netloc and not parsed.username and not parsed.password)):
        raise BadRequestException('Use an HTTP(S) URL or a local absolute path.', 14017)
    return value


def save(data):
    if not isinstance(data, dict) or not isinstance(data.get('brand'), dict) or not isinstance(data.get('pda'), dict):
        raise BadRequestException('Invalid presentation settings.', 14017)
    result = dict(brand={}, pda={}, notices=[])
    for key in BRAND:
        value = data['brand'].get(key, '')
        result['brand'][key] = _text(value, 200) if key in ('productName', 'loginTitle', 'loginSubtitle') else _url(value)
    for key in PDA:
        value = data['pda'].get(key, False if key in ('login', 'header') else '')
        if key in ('login', 'header'):
            if not isinstance(value, bool):
                raise BadRequestException('Invalid display flag.', 14017)
            result['pda'][key] = value
        elif key in ('sizeBytes', 'versionCode'):
            if value in ('', None):
                result['pda'][key] = None
            elif type(value) is not int or not 0 < value <= (10 * 1024**3 if key == 'sizeBytes' else 2147483647):
                raise BadRequestException('Invalid APK file size or version code.', 14017)
            else:
                result['pda'][key] = value
        elif key == 'releasedAt':
            value = _text(value, 50)
            try:
                if value:
                    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    if parsed.tzinfo is None:
                        raise ValueError()
                    value = parsed.isoformat()
            except ValueError:
                raise BadRequestException('APK release time must include a time zone.', 14017)
            result['pda'][key] = value
        elif key == 'releaseDate':
            value = _text(value, 10)
            try:
                if value and (not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) or date.fromisoformat(value).isoformat() != value):
                    raise ValueError()
            except ValueError:
                raise BadRequestException('Invalid APK release date.', 14017)
            result['pda'][key] = value
        elif key == 'sha256':
            value = _text(value, 64)
            if value and not re.fullmatch(r'[0-9a-fA-F]{64}', value):
                raise BadRequestException('Invalid APK SHA-256.', 14017)
            result['pda'][key] = value.lower()
        else:
            result['pda'][key] = _url(value) if key == 'url' else _text(value, 50)
    notices = data.get('notices', [])
    if not isinstance(notices, list) or len(notices) > 50:
        raise BadRequestException('At most 50 notices are allowed.', 14017)
    for row in notices:
        if not isinstance(row, dict) or row.get('kind') not in ('notice', 'important', 'maintenance'):
            raise BadRequestException('Invalid notice kind.', 14017)
        item = dict(text=_text(row.get('text'), 1000), kind=row['kind'])
        if not item['text']:
            raise BadRequestException('Notice text is required.', 14017)
        for key in ('enabled', 'login', 'header'):
            if not isinstance(row.get(key), bool):
                raise BadRequestException('Invalid notice display flag.', 14017)
            item[key] = row[key]
        for key in ('start', 'end'):
            try:
                value = row.get(key)
                item[key] = clock.parse_input_datetime(value, None).isoformat() if value else None
            except (ValueError, TypeError, AttributeError):
                raise BadRequestException('Invalid notice time.', 14017)
        if item['start'] and item['end'] and clock.parse_input_datetime(item['start'], None) >= clock.parse_input_datetime(item['end'], None):
            raise BadRequestException('Notice end must follow its start.', 14017)
        result['notices'].append(item)
    row = SystemSetting.query.filter_by(key=KEY).with_for_update().first()
    previous = json.loads(row.value) if row else DEFAULT
    if type(data.get('revision')) is not int or data['revision'] != previous['revision']:
        raise ConflictException('Settings changed; reload before saving.', 14017)
    result['revision'] = previous['revision'] + 1
    if row:
        row.value = json.dumps(result)
    else:
        db.session.add(SystemSetting(key=KEY, value=json.dumps(result)))
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise ConflictException('Settings changed; reload before saving.', 14017)
    return result
