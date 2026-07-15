from fastapi import APIRouter, HTTPException

from ..services.report_templates.registry import PACKAGE_ID, ReportTemplateUnavailable, report_template_registry

router = APIRouter(prefix="/report-templates", tags=["report-templates"])


def _package(package_id: str):
    if package_id != PACKAGE_ID:
        raise HTTPException(status_code=404, detail={"code": "REPORT_TEMPLATE_NOT_FOUND"})
    try:
        return report_template_registry.load()
    except ReportTemplateUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": exc.code, "asset": exc.asset}) from exc


@router.get("")
def list_report_templates(): return [report_template_registry.status()]


@router.get("/{package_id}")
def get_report_template(package_id: str): return _package(package_id).safe_summary()


@router.get("/{package_id}/fields")
def get_report_template_fields(package_id: str):
    package = _package(package_id)
    return {
        "package_id": package_id,
        "fields": package.fields,
        "rule_contracts": package.rule_contracts,
        "projection_catalog": package.projection_catalog,
    }


@router.get("/{package_id}/rule-hints")
def get_report_template_rule_hints(package_id: str): return {"package_id": package_id, "rules": _package(package_id).rule_hints}


@router.post("/{package_id}/validate")
def validate_report_template(package_id: str): return _package(package_id).safe_summary()
