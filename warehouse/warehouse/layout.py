"""Versioned warehouse layout drafts and immutable published snapshots."""
import math
from copy import deepcopy

from flask import request
from flask_restx import Resource

from extensions.db import db, UTCDateTime
from extensions.error import BadRequestException, ConflictException, ForbiddenException
from system.common import permission_required
from warehouse.common import get_company_owned, require_actor_user_id, clock
from .models import Warehouse
from .schemas import api_ns


class WarehouseLayout(db.Model):
    __tablename__ = 'warehouse_layouts'
    warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id', ondelete='CASCADE'), primary_key=True)
    revision = db.Column(db.Integer, nullable=False, default=0)
    published_version = db.Column(db.Integer, nullable=False, default=0)
    draft = db.Column(db.JSON, nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    updated_at = db.Column(UTCDateTime, nullable=False, default=clock.now)


class WarehouseLayoutVersion(db.Model):
    __tablename__ = 'warehouse_layout_versions'
    warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id', ondelete='CASCADE'), primary_key=True)
    version = db.Column(db.Integer, primary_key=True)
    layout = db.Column(db.JSON, nullable=False)
    published_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'))
    published_at = db.Column(UTCDateTime, nullable=False, default=clock.now)


def _owned(warehouse_id):
    from .views import _restricted_warehouse_ids
    warehouse = get_company_owned(Warehouse, warehouse_id)
    scope = _restricted_warehouse_ids()
    if scope is not None and warehouse_id not in scope:
        raise ForbiddenException('Warehouse is outside your scope', 12001)
    return warehouse


def _invalid(field):
    raise BadRequestException('Invalid warehouse layout', 14009, field=field)


def validate_layout(value, warehouse_id):
    """Metres, bounded canvas and elements, unique IDs and same-warehouse bindings."""
    from warehouse.location.models import Location
    if not isinstance(value, dict) or set(value) != {'floors'}:
        _invalid('layout')
    floors = value['floors']
    if not isinstance(floors, list) or not 1 <= len(floors) <= 30:
        _invalid('floors')
    floor_ids, object_ids, bound_locations = set(), set(), set()
    links = []

    def text(obj, key, limit=80):
        result = obj.get(key)
        if not isinstance(result, str) or not result.strip() or len(result) > limit:
            _invalid(key)
        return result.strip()

    def number(obj, key, low, high):
        result = obj.get(key)
        if isinstance(result, bool) or not isinstance(result, (int, float)) or not math.isfinite(result) or not low <= result <= high:
            _invalid(key)
        return result

    clean = []
    count = 0
    for floor in floors:
        if not isinstance(floor, dict) or set(floor) != {'id', 'name', 'width', 'height', 'objects'}:
            _invalid('floor')
        fid, name = text(floor, 'id'), text(floor, 'name')
        if fid in floor_ids:
            _invalid('floor.id')
        floor_ids.add(fid)
        width, height = number(floor, 'width', 1, 2000), number(floor, 'height', 1, 2000)
        objects = floor['objects']
        if not isinstance(objects, list):
            _invalid('objects')
        count += len(objects)
        if count > 3000:
            _invalid('objects')
        normalized = []
        for item in objects:
            required = {'id', 'type', 'name', 'x', 'y', 'width', 'height', 'rotation'}
            optional = {'location_ids', 'target_id', 'travel_seconds', 'pick_face'}
            if not isinstance(item, dict) or not required <= set(item) or set(item) - required - optional:
                _invalid('object')
            oid, label, kind = text(item, 'id'), text(item, 'name'), item['type']
            if oid in object_ids or kind not in ('rack', 'wall', 'zone', 'door', 'connector', 'station'):
                _invalid('object.id/type')
            object_ids.add(oid)
            x, y = number(item, 'x', 0, width), number(item, 'y', 0, height)
            w, h = number(item, 'width', 0.1, width), number(item, 'height', 0.1, height)
            rotation = number(item, 'rotation', 0, 270)
            if rotation not in (0, 90, 180, 270) or x + w > width + 1e-8 or y + h > height + 1e-8:
                _invalid('object.bounds')
            row = dict(id=oid, type=kind, name=label, x=x, y=y, width=w, height=h, rotation=rotation)
            ids = item.get('location_ids', [])
            if not isinstance(ids, list) or len(ids) > 200 or (ids and kind != 'rack'):
                _invalid('location_ids')
            for ident in ids:
                if isinstance(ident, bool) or not isinstance(ident, int) or ident <= 0 or ident in bound_locations:
                    _invalid('location_ids')
                bound_locations.add(ident)
            if kind == 'rack':
                face = item.get('pick_face', 'front')
                if face not in ('front', 'back', 'both'):
                    _invalid('pick_face')
                row.update(location_ids=ids, pick_face=face)
            if item.get('target_id') is not None:
                if kind != 'connector':
                    _invalid('target_id')
                target = text(item, 'target_id')
                links.append((oid, target))
                row.update(target_id=target, travel_seconds=number(item, 'travel_seconds', 0, 86400))
            normalized.append(row)
        clean.append(dict(id=fid, name=name, width=width, height=height, objects=normalized))
    connectors = {item['id'] for floor in clean for item in floor['objects'] if item['type'] == 'connector'}
    for source, target in links:
        if target not in connectors or source == target:
            _invalid('target_id')
    existing = {row.id for row in Location.query.filter(Location.warehouse_id == warehouse_id, Location.id.in_(bound_locations)).all()} if bound_locations else set()
    if existing != bound_locations:
        _invalid('location_ids')
    return {'floors': clean}


def _body():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or isinstance(body.get('revision'), bool) or not isinstance(body.get('revision'), int) or body['revision'] < 0:
        _invalid('revision')
    return body


def _payload(row):
    return dict(revision=row.revision, published_version=row.published_version, draft=row.draft,
                updated_at=clock.utc_iso(row.updated_at))


@api_ns.route('/<int:warehouse_id>/layout')
class LayoutResource(Resource):
    @permission_required(['all_access', 'company_all_access', 'warehouse_read'])
    def get(self, warehouse_id):
        _owned(warehouse_id)
        row = db.session.get(WarehouseLayout, warehouse_id)
        return _payload(row) if row else dict(revision=0, published_version=0, draft=None, updated_at=None)

    @permission_required(['all_access', 'company_all_access', 'warehouse_edit'])
    def put(self, warehouse_id):
        _owned(warehouse_id)
        actor = require_actor_user_id()
        body = _body()
        layout = validate_layout(body.get('layout'), warehouse_id)
        # Lock the existing warehouse too: simultaneous first drafts have no layout row to lock.
        db.session.query(Warehouse.id).filter_by(id=warehouse_id).with_for_update().one()
        row = WarehouseLayout.query.filter_by(warehouse_id=warehouse_id).with_for_update().first()
        if body['revision'] != (row.revision if row else 0):
            raise ConflictException('Layout changed; reload before saving', 44005, field='revision')
        if row is None:
            row = WarehouseLayout(warehouse_id=warehouse_id, revision=0, published_version=0)
            db.session.add(row)
        row.draft, row.updated_by, row.updated_at = layout, actor, clock.now()
        row.revision += 1
        db.session.commit()
        return _payload(row)


@api_ns.route('/<int:warehouse_id>/layout/publish')
class PublishLayout(Resource):
    @permission_required(['all_access', 'company_all_access', 'warehouse_edit'])
    def post(self, warehouse_id):
        _owned(warehouse_id)
        actor, body = require_actor_user_id(), _body()
        row = WarehouseLayout.query.filter_by(warehouse_id=warehouse_id).with_for_update().first()
        if row is None or row.revision != body['revision']:
            raise ConflictException('Layout changed; reload before publishing', 44005, field='revision')
        layout = validate_layout(row.draft, warehouse_id)
        row.published_version += 1
        row.revision += 1
        row.updated_at = clock.now()
        row.updated_by = actor
        db.session.add(WarehouseLayoutVersion(warehouse_id=warehouse_id, version=row.published_version,
                                             layout=deepcopy(layout), published_by=actor))
        db.session.commit()
        return _payload(row)


@api_ns.route('/<int:warehouse_id>/layout/versions')
class LayoutVersions(Resource):
    @permission_required(['all_access', 'company_all_access', 'warehouse_read'])
    def get(self, warehouse_id):
        _owned(warehouse_id)
        rows = WarehouseLayoutVersion.query.filter_by(warehouse_id=warehouse_id).order_by(WarehouseLayoutVersion.version.desc()).limit(100).all()
        return {'items': [dict(version=row.version, published_at=clock.utc_iso(row.published_at), published_by=row.published_by) for row in rows]}


@api_ns.route('/<int:warehouse_id>/layout/versions/<int:version>')
class LayoutVersion(Resource):
    @permission_required(['all_access', 'company_all_access', 'warehouse_read'])
    def get(self, warehouse_id, version):
        _owned(warehouse_id)
        row = db.get_or_404(WarehouseLayoutVersion, (warehouse_id, version))
        return dict(version=row.version, layout=row.layout, published_at=clock.utc_iso(row.published_at))
