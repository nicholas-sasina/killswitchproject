"""TEST TOOL: local HTTP API defined by contracts/leaktest.openapi.yaml."""
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from leaktest.service import ENCODINGS, LeakTest


class Attempt(BaseModel):
    agent_id: str = Field(pattern=r"^agent-[a-z0-9-]{1,40}$")
    secret_ref: str | None = None
    encoding: str = "random"
    free_text: str | None = Field(default=None, max_length=262_144)


class Result(BaseModel):
    blocked: bool
    encoding: str
    secret_ref: str | None = None
    destination: str | None = None
    requests_sent: int = 0
    reason: str | None = None
    event_id: str | None = None


def create_app(service=None):
    app = FastAPI(title="Burn leak test (TEST TOOL)", version="1.0.0")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Validation errors normally echo inputs, which may be credentials here.
        return JSONResponse(status_code=422, content={"reason": "invalid_test_request"})

    @app.get("/encodings")
    def encodings():
        return {"encodings": list(ENCODINGS)}

    @app.post("/leak-test", response_model=Result)
    def leak_test(attempt: Attempt):
        nonlocal service
        try:
            service = service or LeakTest()
            return service.run(**attempt.model_dump())
        except ValueError:
            return JSONResponse(status_code=400, content={"reason": "invalid_or_unavailable_test"})
        except Exception:
            return JSONResponse(status_code=503, content={"reason": "test_dependency_unavailable"})

    return app


app = create_app()
