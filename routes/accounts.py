"""
Accounts routes — /api/accounts, /api/stats, /api/tags, /api/industries,
index page, company page, data dictionary, tier view.
"""

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from classifier import ALL_INDUSTRIES
from database import (
    get_all_enrichments,
    get_all_tags,
    get_all_dd_comments,
    get_default_scoring_profile,
    get_default_scoring_profile_id,
    get_scoring_profile_by_id,
    get_scoring_profile_for_user,
    list_all_scoring_profiles,
    list_scoring_profiles_for_user,
    upsert_dd_comment,
    upsert_enrichment,
)
from scoring import compute_score
from state import ACCOUNTS
from utils import sanitize, normalize_text
from routes.auth import login_required

accounts_bp = Blueprint("accounts", __name__)


# --- Scoring helpers ---


def get_profile_for_user(user: dict, profile_id: int) -> dict | None:
    profile = get_scoring_profile_for_user(profile_id, user["id"])
    if profile:
        return profile
    if user["role"] == "super_admin":
        return get_scoring_profile_by_id(profile_id, current_user_id=user["id"])
    return None


def get_active_scoring_profile(user: dict) -> dict:
    """Return active scoring profile for user (session override, else global default)."""
    profile = None
    selected_id = session.get("active_scoring_profile_id")
    manual_selection = bool(session.get("active_scoring_profile_manual"))

    if manual_selection and selected_id is not None:
        try:
            profile = get_profile_for_user(user, int(selected_id))
        except (TypeError, ValueError):
            profile = None

    if not profile:
        default_id = get_default_scoring_profile_id()
        if default_id is not None:
            profile = get_profile_for_user(user, default_id)

    if not profile:
        if user["role"] == "super_admin":
            profiles = list_all_scoring_profiles(current_user_id=user["id"])
        else:
            profiles = list_scoring_profiles_for_user(user["id"])
        if profiles:
            profile = profiles[0]

    if not profile:
        raise RuntimeError("No scoring profile available.")

    session["active_scoring_profile_id"] = profile["id"]
    if not manual_selection:
        session["active_scoring_profile_manual"] = False
    return profile


def score_account_with_profile(account: dict, profile: dict) -> dict:
    scored = dict(account)
    score_result = compute_score(
        scored,
        weights=profile["weights"],
        industry_scores=profile["industry_scores"],
    )
    scored["score"] = score_result["composite"]
    scored["tier"] = score_result["tier"]
    scored["score_breakdown"] = score_result["breakdown"]
    return scored


def get_scored_accounts(profile: dict) -> list[dict]:
    return [score_account_with_profile(a, profile) for a in ACCOUNTS.values()]


# --- UI routes ---


@accounts_bp.route("/")
@login_required
def index():
    return render_template("index.html", current_user=request.current_user)


@accounts_bp.route("/scoring-profiles")
@login_required
def scoring_profiles_page():
    return render_template("scoring_profiles.html", current_user=request.current_user)


@accounts_bp.route("/tier/<tier_letter>")
@login_required
def tier_page(tier_letter):
    tier_letter = tier_letter.upper()
    if tier_letter not in ("A", "B", "C", "D", "E"):
        return redirect(url_for("accounts.index"))
    return render_template("tier_view.html", current_user=request.current_user, tier=tier_letter)


@accounts_bp.route("/data-dictionary")
@login_required
def data_dictionary_page():
    return render_template("data_dictionary.html", current_user=request.current_user)


# --- API routes ---


@accounts_bp.route("/api/accounts")
@login_required
def api_accounts():
    profile = get_active_scoring_profile(request.current_user)
    accounts_list = sorted(get_scored_accounts(profile), key=lambda a: a["score"], reverse=True)
    for i, account in enumerate(accounts_list):
        account["rank"] = i + 1
    return jsonify(sanitize(accounts_list))


@accounts_bp.route("/api/accounts/<int:bp_id>")
@login_required
def api_account_detail(bp_id):
    profile = get_active_scoring_profile(request.current_user)
    account = ACCOUNTS.get(bp_id)
    if not account:
        return jsonify({"error": "Account not found"}), 404
    return jsonify(sanitize(score_account_with_profile(account, profile)))


@accounts_bp.route("/api/accounts/<int:bp_id>/update", methods=["POST"])
@login_required
def api_update_account(bp_id):
    if bp_id not in ACCOUNTS:
        return jsonify({"error": "Account not found"}), 404

    from flask import request as req
    data = req.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid JSON payload"}), 400

    account = ACCOUNTS[bp_id]
    db_fields = {}

    if "industry_override" in data:
        db_fields["industry_override"] = data["industry_override"]
        account["industry"] = data["industry_override"]
        account["industry_source"] = "manual"

    if "notes" in data:
        db_fields["notes"] = data["notes"]
        account["notes"] = data["notes"]

    if "starred" in data:
        db_fields["starred"] = 1 if data["starred"] else 0
        account["starred"] = bool(data["starred"])

    if "tags" in data:
        db_fields["tags"] = data["tags"]
        account["tags"] = data["tags"]

    if "target_list" in data:
        db_fields["target_list"] = 1 if data["target_list"] else 0
        account["target_list"] = bool(data["target_list"])

    if "employee_count" in data:
        db_fields["employee_count"] = data["employee_count"]
        account["employee_count"] = data["employee_count"]

    if "website" in data:
        db_fields["website"] = data["website"]
        account["website"] = data["website"]

    if "city" in data:
        db_fields["city"] = data["city"]
        account["city"] = data["city"]

    if db_fields:
        upsert_enrichment(bp_id, **db_fields)

    profile = get_active_scoring_profile(request.current_user)
    return jsonify(sanitize(score_account_with_profile(account, profile)))


@accounts_bp.route("/api/stats")
@login_required
def api_stats():
    profile = get_active_scoring_profile(request.current_user)
    accounts = get_scored_accounts(profile)
    total = len(accounts)

    tiers = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
    for a in accounts:
        tier = a.get("tier", "E")
        if tier not in tiers:
            tiers[tier] = 0
        tiers[tier] += 1

    industries = {}
    for a in accounts:
        industries[a["industry"]] = industries.get(a["industry"], 0) + 1

    sap_statuses = {}
    for a in accounts:
        sap_statuses[a["sap_status"]] = sap_statuses.get(a["sap_status"], 0) + 1

    score_dist = {f"{i}-{i + 9}": 0 for i in range(0, 100, 10)}
    score_dist["100"] = 0
    for a in accounts:
        score_value = float(a.get("score") or 0)
        if score_value >= 100:
            score_dist["100"] += 1
            continue
        bucket = min(max(int(score_value // 10) * 10, 0), 90)
        score_dist[f"{bucket}-{bucket + 9}"] += 1

    avg_score = sum(a["score"] for a in accounts) / total if total else 0
    starred_count = sum(1 for a in accounts if a["starred"])
    target_count = sum(1 for a in accounts if a["target_list"])

    return jsonify(
        {
            "total": total,
            "tiers": tiers,
            "industries": dict(sorted(industries.items(), key=lambda x: -x[1])),
            "sap_statuses": sap_statuses,
            "score_distribution": score_dist,
            "avg_score": round(avg_score, 1),
            "starred_count": starred_count,
            "target_count": target_count,
        }
    )


@accounts_bp.route("/api/industries")
@login_required
def api_industries():
    return jsonify(ALL_INDUSTRIES)


@accounts_bp.route("/api/tags")
@login_required
def api_tags():
    return jsonify(get_all_tags())


# --- Data Dictionary API ---


@accounts_bp.route("/api/data-dictionary")
@login_required
def api_data_dictionary():
    excel_fields = [
        {"field_key": "s1_bp_id", "excel_column": "Business Partner ID", "sheet": "Both", "stored": True,
         "internal_name": "bp_id", "storage_type": "transformed",
         "transform_notes": "Convertido a entero. Las filas sin este valor se descartan. Se usa como clave de union entre hojas.",
         "description": "Identificador unico de Business Partner en SAP para cada cuenta."},
        {"field_key": "s1_erp_isp_id", "excel_column": "ERP ISP ID", "sheet": "Sheet1", "stored": True,
         "internal_name": "erp_isp_id", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto. Tambien se usa para derivar el campo sap_status (con valor = 'Existing SAP', vacio = 'Net New').",
         "description": "Identificador ERP ISP. Indica si la cuenta tiene una relacion existente con SAP."},
        {"field_key": "s1_company_name", "excel_column": "Organization Name1", "sheet": "Both", "stored": True,
         "internal_name": "company_name", "storage_type": "transformed",
         "transform_notes": "Se convierte a titulo si esta en mayusculas (preservando acronimos como SA, DE, CV). Se prefiere el valor de Hoja 2 sobre Hoja 1.",
         "description": "Nombre legal o registrado de la empresa."},
        {"field_key": "s1_planning_entity", "excel_column": "Planning Entity", "sheet": "Sheet1", "stored": True,
         "internal_name": "planning_entity_id", "storage_type": "transformed",
         "transform_notes": "Convertido a entero.",
         "description": "Identificador numerico de la entidad de planificacion."},
        {"field_key": "s1_planning_entity_name", "excel_column": "Planning Entity Name", "sheet": "Both", "stored": True,
         "internal_name": "planning_entity_name", "storage_type": "transformed",
         "transform_notes": "Se convierte a titulo si esta en mayusculas. Se prefiere el valor de Hoja 2 sobre Hoja 1.",
         "description": "Nombre de la entidad de planificacion a la que pertenece la cuenta."},
        {"field_key": "s1_region", "excel_column": "Default Address Region Descr", "sheet": "Both", "stored": True,
         "internal_name": "region", "storage_type": "transformed",
         "transform_notes": "Se prefiere el valor de Hoja 2. Si es nulo, se usa 'Jalisco' por defecto.",
         "description": "Descripcion de la region geografica de la direccion predeterminada de la cuenta."},
        {"field_key": "s1_account_exec", "excel_column": "Account Executive Name 2026", "sheet": "Both", "stored": True,
         "internal_name": "account_exec", "storage_type": "renamed",
         "transform_notes": "Se prefiere el valor de Hoja 2 sobre Hoja 1.",
         "description": "Nombre del ejecutivo de cuenta asignado para la planificacion 2026."},
        {"field_key": "s1_master_industry", "excel_column": "Default Master Code Descr", "sheet": "Both", "stored": True,
         "internal_name": "master_industry", "storage_type": "transformed",
         "transform_notes": "Se prefiere Hoja 2. Si es nulo, se usa 'Unclassified'. Se utiliza como entrada para el algoritmo de clasificacion de industria.",
         "description": "Descripcion del codigo maestro de industria de SAP (clasificacion cruda de industria de SAP)."},
        {"field_key": "s1_sic_description", "excel_column": "Default SIC Primary Descr", "sheet": "Both", "stored": True,
         "internal_name": "sic_description", "storage_type": "transformed",
         "transform_notes": "Se prefiere Hoja 2. Si es nulo, queda como texto vacio. Se usa como entrada para clasificacion de industria por palabras clave.",
         "description": "Descripcion primaria de la Clasificacion Industrial Estandar (SIC)."},
        {"field_key": "s1_base_instalada", "excel_column": "BASE INSTALADA", "sheet": "Sheet1", "stored": True,
         "internal_name": "base_instalada", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto. Si es nulo, queda vacio. Nota: el nombre de la columna puede tener un espacio al final en Excel.",
         "description": "Lista de productos SAP instalados para esta cuenta (base instalada)."},
        {"field_key": "s2_tax_number", "excel_column": "Account Main Tax Number", "sheet": "Sheet2", "stored": True,
         "internal_name": "tax_number", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Numero de identificacion fiscal principal (RFC) de la cuenta."},
        {"field_key": "s2_top_parent_name", "excel_column": "SAP Top Parent Name", "sheet": "Sheet2", "stored": True,
         "internal_name": "top_parent_name", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Nombre de la empresa matriz de nivel superior en la jerarquia corporativa de SAP."},
        {"field_key": "s2_address_street", "excel_column": "Default Address Street", "sheet": "Sheet2", "stored": True,
         "internal_name": "address_street", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Calle de la direccion predeterminada de la cuenta."},
        {"field_key": "s2_address_city", "excel_column": "Default Address City", "sheet": "Sheet2", "stored": True,
         "internal_name": "address_city / city", "storage_type": "transformed",
         "transform_notes": "Se guarda como texto. Se usa como respaldo para el campo 'city'; el valor de enriquecimiento en BD tiene prioridad si existe.",
         "description": "Ciudad de la direccion predeterminada de la cuenta."},
        {"field_key": "s2_address_region_code", "excel_column": "Default Address Region", "sheet": "Sheet2", "stored": True,
         "internal_name": "address_region_code", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Codigo de region (ISO o local) de la direccion predeterminada de la cuenta."},
        {"field_key": "s2_address_postal_code", "excel_column": "Default Address Postal Code", "sheet": "Sheet2", "stored": True,
         "internal_name": "address_postal_code", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Codigo postal de la direccion predeterminada de la cuenta."},
        {"field_key": "s2_account_owner_id", "excel_column": "Account Owner ID", "sheet": "Sheet2", "stored": True,
         "internal_name": "account_owner_id", "storage_type": "transformed",
         "transform_notes": "Convertido a entero.",
         "description": "Identificador numerico del propietario/responsable de la cuenta."},
        {"field_key": "s2_turnover_usd", "excel_column": "Turnover US Dollar Value", "sheet": "Sheet2", "stored": True,
         "internal_name": "revenue", "storage_type": "transformed",
         "transform_notes": "Convertido a decimal. Renombrado de 'turnover_usd' a 'revenue'. El valor de enriquecimiento en BD tiene prioridad si existe.",
         "description": "Facturacion/ingresos anuales en dolares estadounidenses."},
        {"field_key": "s2_num_employees", "excel_column": "Num Employees Local Value", "sheet": "Sheet2", "stored": True,
         "internal_name": "employee_count", "storage_type": "transformed",
         "transform_notes": "Convertido a entero. Renombrado de 'num_employees' a 'employee_count'. El valor de enriquecimiento en BD tiene prioridad si existe.",
         "description": "Numero de empleados en la entidad local."},
        {"field_key": "s2_market_segment", "excel_column": "Internal Market Segment IMS Desc", "sheet": "Sheet2", "stored": True,
         "internal_name": "market_segment", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Descripcion de la clasificacion de Segmento de Mercado Interno (IMS) de SAP."},
        {"field_key": "s2_bpr_products", "excel_column": "Buying Product Relationship BPR Descr Concat", "sheet": "Sheet2", "stored": True,
         "internal_name": "bpr_products", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto. Si es nulo, queda vacio.",
         "description": "Lista concatenada de Relaciones de Compra de Productos (BPR) — los productos SAP que la cuenta ha comprado o con los que esta asociada."},
        {"field_key": "s2_archetype", "excel_column": "Archetype Descr Plan 2026", "sheet": "Sheet2", "stored": True,
         "internal_name": "archetype", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Clasificacion de arquetipo de la cuenta para el plan 2026."},
        {"field_key": "s2_rbc_plan", "excel_column": "RBC 2026 PLAN", "sheet": "Sheet2", "stored": True,
         "internal_name": "rbc_plan", "storage_type": "as-is",
         "transform_notes": "Se guarda como texto.",
         "description": "Asignacion de plan de Clasificacion Basada en Ingresos (RBC) para 2026."},
        {"field_key": "s2_master_code", "excel_column": "Default Master Code", "sheet": "Sheet2", "stored": True,
         "internal_name": "master_code", "storage_type": "transformed",
         "transform_notes": "Convertido a entero. Se usa para clasificacion de industria mediante mapeo de codigos maestros (27 codigos SAP → 16 categorias de industria).",
         "description": "Codigo maestro numerico de SAP utilizado para la clasificacion de industria."},
        {"field_key": "s2_xlsx_web_address", "excel_column": "Account Web Address", "sheet": "Sheet2", "stored": True,
         "internal_name": "xlsx_web_address / website", "storage_type": "transformed",
         "transform_notes": "Se guarda como texto. Se usa como respaldo para el campo 'website'; el valor de enriquecimiento en BD tiene prioridad si existe.",
         "description": "URL del sitio web de la empresa segun registro en SAP."},
    ]

    derived_fields = [
        {"field_key": "d_display_name", "internal_name": "display_name", "source": "Calculado",
         "description": "Nombre preferido para mostrar: el mas largo entre company_name y planning_entity_name.",
         "transform_notes": "Se compara por longitud de texto al momento de cargar."},
        {"field_key": "d_sap_status", "internal_name": "sap_status", "source": "Derivado de erp_isp_id",
         "description": "Estado de la relacion SAP de la cuenta.",
         "transform_notes": "'Existing SAP' si erp_isp_id tiene valor, 'Net New' en caso contrario."},
        {"field_key": "d_industry", "internal_name": "industry", "source": "Clasificado",
         "description": "Categoria de industria normalizada (una de 16 categorias + Sin Clasificar).",
         "transform_notes": "Cascada: override manual → mapeo de master_code (27 codigos) → coincidencia por palabras clave (espanol e ingles) → 'Unclassified'."},
        {"field_key": "d_industry_source", "internal_name": "industry_source", "source": "Clasificado",
         "description": "Como se determino la clasificacion de industria.",
         "transform_notes": "Valores: 'manual' (override de usuario), 'master_code', 'keyword', 'none'."},
        {"field_key": "d_score", "internal_name": "score", "source": "Calculado por solicitud",
         "description": "Puntaje compuesto de la cuenta (0–100) basado en el perfil de scoring activo.",
         "transform_notes": "Suma ponderada de: industry_match, company_size, sap_relationship, data_completeness."},
        {"field_key": "d_tier", "internal_name": "tier", "source": "Calculado por solicitud",
         "description": "Nivel por letra (A/B/C/D/E) derivado del puntaje compuesto.",
         "transform_notes": "A ≥ 80, B ≥ 60, C ≥ 40, D ≥ 20, E < 20 (umbrales del perfil de scoring)."},
        {"field_key": "d_notes", "internal_name": "notes", "source": "BD (account_enrichments)",
         "description": "Notas de texto libre agregadas por usuarios sobre la cuenta.",
         "transform_notes": "Almacenado en tabla SQLite account_enrichments, no proviene del Excel."},
        {"field_key": "d_starred", "internal_name": "starred", "source": "BD (account_enrichments)",
         "description": "Indicador booleano de si un usuario ha marcado como favorita la cuenta.",
         "transform_notes": "Almacenado como entero (0/1) en BD, convertido a booleano."},
        {"field_key": "d_tags", "internal_name": "tags", "source": "BD (account_enrichments)",
         "description": "Etiquetas definidas por el usuario para categorizar cuentas.",
         "transform_notes": "Almacenado como arreglo JSON en BD."},
        {"field_key": "d_target_list", "internal_name": "target_list", "source": "BD (account_enrichments)",
         "description": "Indicador booleano de si la cuenta esta en la lista objetivo.",
         "transform_notes": "Almacenado como entero (0/1) en BD, convertido a booleano."},
        {"field_key": "d_website", "internal_name": "website", "source": "Enriquecimiento BD o xlsx_web_address",
         "description": "URL del sitio web de la empresa (enriquecido o respaldo del Excel).",
         "transform_notes": "Prioridad: BD account_enrichments.website → columna 'Account Web Address' del Excel."},
        {"field_key": "d_city", "internal_name": "city", "source": "Enriquecimiento BD o address_city",
         "description": "Ciudad de la empresa (enriquecido o respaldo del Excel).",
         "transform_notes": "Prioridad: BD account_enrichments.city → columna 'Default Address City' del Excel."},
    ]

    comments = get_all_dd_comments()
    for f in excel_fields + derived_fields:
        c = comments.get(f["field_key"], {})
        f["comment"] = c.get("comment", "")
        f["comment_updated_by"] = c.get("updated_by", "")
        f["comment_updated_at"] = c.get("updated_at", "")

    return jsonify({"excel_fields": excel_fields, "derived_fields": derived_fields})


@accounts_bp.route("/api/data-dictionary/<field_key>/comment", methods=["PUT"])
@login_required
def api_data_dictionary_comment(field_key):
    data = request.get_json(force=True)
    comment = data.get("comment", "").strip()
    upsert_dd_comment(field_key, comment, request.current_user["email"])
    return jsonify({"ok": True})
