"""
Routes package — Flask Blueprints for all application routes.
"""

from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.accounts import accounts_bp
from routes.crm import crm_bp
from routes.pipeline import pipeline_bp
from routes.scoring import scoring_bp
from routes.export import export_bp

ALL_BLUEPRINTS = [
    auth_bp,
    admin_bp,
    accounts_bp,
    crm_bp,
    pipeline_bp,
    scoring_bp,
    export_bp,
]
