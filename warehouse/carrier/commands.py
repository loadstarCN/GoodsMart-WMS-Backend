"""承运商 CLI 命令"""
import os

import click
from flask.cli import AppGroup

from extensions.db import db
from .integration_services import CarrierIntegrationService
from .integrations.fedex import FedexAdapter
from .models import Carrier

carrier_cli = AppGroup('carrier', help='Carrier management commands')

# 旧版环境变量 → FedEx 对接设置项
_LEGACY_SETTINGS = {
    'FEDEX_SERVICE_TYPE': 'service_type',
    'FEDEX_PICKUP_TYPE': 'pickup_type',
    'FEDEX_DUTIES_PAYMENT_TYPE': 'duties_payment_type',
    'FEDEX_DEFAULT_LABEL_FORMAT': 'default_label_format',
    'FEDEX_LABEL_THERMAL_IMAGE_TYPE': 'thermal_image_type',
    'FEDEX_LABEL_THERMAL_PAGE_SIZE': 'thermal_page_size',
}


def _env(name):
    return (os.environ.get(name) or '').strip()


@carrier_cli.command('import-fedex-env')
@click.option('--carrier-id', type=int, help='Import into this carrier only (default: carriers with code "fedex" '
                                             'in the companies of FEDEX_ALLOWED_COMPANY_IDS)')
@click.option('--overwrite', is_flag=True, help='Replace an integration that is already set up')
@click.option('--dry-run', is_flag=True, help='Only show what would be imported')
def import_fedex_env(carrier_id, overwrite, dry_run):
    """把旧版的 FEDEX_* 环境变量导入到承运商的在线对接设置（升级到按承运商设置后执行一次）"""
    api_key, secret_key, account = _env('FEDEX_API_KEY'), _env('FEDEX_SECRET_KEY'), _env('FEDEX_ACCOUNT_NUMBER')
    if not (api_key and secret_key and account):
        raise click.ClickException('FEDEX_API_KEY, FEDEX_SECRET_KEY and FEDEX_ACCOUNT_NUMBER must be set')

    if carrier_id:
        carriers = [c for c in [db.session.get(Carrier, carrier_id)] if c is not None]
        if not carriers:
            raise click.ClickException(f'Carrier {carrier_id} not found')
    else:
        company_ids = [int(x) for x in _env('FEDEX_ALLOWED_COMPANY_IDS').replace(' ', '').split(',') if x.isdigit()]
        if not company_ids:
            raise click.ClickException('FEDEX_ALLOWED_COMPANY_IDS is empty: pass --carrier-id')
        carriers = (Carrier.query
                    .filter(Carrier.company_id.in_(company_ids), db.func.lower(Carrier.code) == 'fedex')
                    .order_by(Carrier.id).all())
        if not carriers:
            raise click.ClickException('No carrier with code "fedex" in companies '
                                       f'{", ".join(map(str, company_ids))}: pass --carrier-id')

    settings = {
        'environment': 'sandbox' if 'sandbox' in (_env('FEDEX_API_BASE') or 'sandbox').lower() else 'production',
        'account_number': account,
        'etd_enabled': _env('FEDEX_ETD_ENABLED').lower() in ('1', 'true', 'yes', 'on'),
    }
    for env_name, key in _LEGACY_SETTINGS.items():
        value = _env(env_name).upper() if key != 'thermal_page_size' else _env(env_name).lower().replace(' ', '')
        if not value:
            continue
        try:
            settings.update(FedexAdapter.clean_settings({key: value}))
        except ValueError as exc:
            click.echo(f'  ! {env_name}={value} is not supported ({exc.args[-1]}); the default is used')

    for carrier in carriers:
        if carrier.integration is not None and not overwrite:
            click.echo(f'- {carrier.name} (#{carrier.id}): already has a {carrier.integration.adapter} '
                       'integration, skipped (use --overwrite)')
            continue
        click.echo(f'- {carrier.name} (#{carrier.id}, company {carrier.company_id}): FedEx '
                   f'{settings["environment"]}, account {account}')
        if dry_run:
            continue
        CarrierIntegrationService.save(carrier, {
            'adapter': FedexAdapter.code, 'enabled': True, 'settings': settings,
            'secrets': {'api_key': api_key, 'secret_key': secret_key},
        }, None)
    click.echo('Dry run, nothing saved.' if dry_run else 'Done. The FEDEX_* variables are no longer read and can be '
                                                        'removed from .env.')
