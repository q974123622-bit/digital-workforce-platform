from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .database import Base, engine
from .routers import audit, chat, employees, internal, knowledge, plugins, policies, teams
from .seed import seed_if_empty


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    seed_if_empty()
    yield


app = FastAPI(title="数字员工平台 Demo API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error_code(status_code: int) -> str:
    return {
        400: "VALIDATION_ERROR",
        403: "POLICY_DENIED",
        404: "NOT_FOUND",
        409: "STATE_CONFLICT",
        422: "VALIDATION_ERROR",
    }.get(status_code, f"HTTP_{status_code}")


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else None
    message = detail.get("message") if isinstance(detail, dict) else str(exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": _error_code(exc.status_code),
                "message": message,
                "detail": detail,
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "请求参数不合法",
                "detail": exc.errors(),
            }
        },
    )


@app.get("/health")
def health():
    return {"status": "ok", "service": "digital-workforce-platform"}


API_PREFIX = "/api/v1"
app.include_router(employees.router, prefix=API_PREFIX)
app.include_router(plugins.router, prefix=API_PREFIX)
app.include_router(policies.router, prefix=API_PREFIX)
app.include_router(audit.router, prefix=API_PREFIX)
app.include_router(teams.router, prefix=API_PREFIX)
app.include_router(teams.tasks_router, prefix=API_PREFIX)
app.include_router(knowledge.router, prefix=API_PREFIX)
app.include_router(chat.router, prefix=API_PREFIX)
app.include_router(internal.router)
