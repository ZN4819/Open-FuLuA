from typing import Any

from fastapi import APIRouter

from ..services.template_profile import load_template_profile

router = APIRouter(prefix="/template-profile", tags=["template-profile"])


@router.get("")
def get_template_profile() -> dict[str, Any]:
    return load_template_profile()
