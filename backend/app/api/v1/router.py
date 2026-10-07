from fastapi import APIRouter

from app.api.v1 import (
    admin,
    agent,
    auth,
    cables,
    component_intelligence,
    component_relations,
    dashboard,
    engineering_evidence,
    files,
    inventory,
    materials,
    navigation_lab,
    picking,
    products,
    projects,
    resources,
    spatial,
    warehouse_maps,
)

api_router = APIRouter()
for router in [
    auth.router,
    agent.router,
    component_intelligence.router,
    component_relations.router,
    engineering_evidence.router,
    materials.router,
    cables.router,
    inventory.router,
    inventory.read_router,
    resources.router,
    warehouse_maps.router,
    navigation_lab.router,
    spatial.router,
    products.router,
    picking.router,
    projects.router,
    files.router,
    dashboard.router,
    admin.router,
]:
    api_router.include_router(router)
