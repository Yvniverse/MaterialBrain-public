import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.core.exceptions import BusinessError
from app.seed.defaults import seed_defaults

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_defaults(db)
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))[:64]
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.exception_handler(BusinessError)
async def business_error(request: Request, exc: BusinessError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "details": exc.details,
            "request_id": request.state.request_id,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": "输入数据校验失败",
            "details": {"errors": jsonable_encoder(exc.errors())},
            "request_id": request.state.request_id,
        },
    )


@app.get("/api/v1/health", tags=["系统"])
def health():
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok", "environment": settings.environment}


@app.get("/api/v1/runtime-info", tags=["系统"])
def runtime_info():
    return {
        "backend_build_sha": settings.materialbrain_build_sha,
        "release_tag": settings.materialbrain_release_tag or None,
        "build_time_utc": settings.materialbrain_build_time_utc or None,
        "environment": settings.environment,
    }


app.include_router(api_router, prefix="/api/v1")
