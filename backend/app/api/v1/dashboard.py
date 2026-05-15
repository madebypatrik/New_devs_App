from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
import app.services.cache as cache
from app.core.auth import authenticate_request as get_current_user

router = APIRouter()

@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str,
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:

    tenant_id = getattr(current_user, "tenant_id", None) or "default_tenant"

    revenue_data = await cache.get_revenue_summary(property_id, tenant_id)

    return {
        "property_id": revenue_data['property_id'],
        "total_revenue": str(revenue_data['total']),
        "currency": revenue_data['currency'],
        "reservations_count": revenue_data['count']
    }
