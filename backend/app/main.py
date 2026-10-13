"""Authenticated legacy API plus additive sessions, proposals and scans."""
import json
import queue
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import date as Date
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.exc import SQLAlchemyError

from agent import coach
from app import db, services as svc, schemas as sh
from app.config import settings
from app.idempotency import digest, mutate, claim, release
from app.models import ChatMessage, DietPlan, Goal, Workout, WorkoutSession, Proposal, FoodLog
from app.today import build_today
from app.visualize import mint_session, SessionToken

@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        db.close_engine()


app = FastAPI(title="Milo", version="2.0.0", lifespan=lifespan)
app.state.session_factory = db.session_factory
bearer = HTTPBearer(auto_error=False)

def authorize(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    if not settings.demo_api_token:
        raise HTTPException(503, "demo_access_not_configured")
    if (credentials is None or credentials.scheme.lower() != "bearer" or
            not secrets.compare_digest(credentials.credentials.encode(), settings.demo_api_token.encode())):
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})

api = APIRouter(dependencies=[Depends(authorize)])
Key = Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=200)]

def factory():
    return app.state.session_factory

def write(request, key, body, operation):
    fingerprint = digest({"method": request.method, "path": request.url.path, "body": body})
    return mutate(factory(), key, fingerprint, operation)

def read(operation):
    with factory()() as s:
        return operation(s)

@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(status_code=422, content={"detail": "validation_error",
        "errors": [{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()]})

@app.exception_handler(SQLAlchemyError)
async def database_error(request, exc):
    return JSONResponse(status_code=503, content={"detail": "database_unavailable"})

@app.exception_handler(RuntimeError)
async def configuration_error(request, exc):
    return JSONResponse(status_code=503, content={"detail": "service_unavailable"})

@app.get("/health", response_model=sh.OK)
def health():
    return {"ok": True}

@api.get("/profile", response_model=sh.UserOut)
def get_profile():
    return read(lambda s: svc.public(svc.profile(s)))

@api.post("/profile", response_model=sh.UserOut)
def post_profile(body: sh.ProfileIn, request: Request, key: Key = None):
    return write(request, key, body.model_dump(mode="json"), lambda s: svc.save_profile(s, body))

@api.get("/today", response_model=sh.TodayOut)
def today(date: Date | None = None):
    return read(lambda s: build_today(s, date))

@api.post("/chat", response_model=sh.ChatOut, response_model_exclude_none=True)
def chat(body: sh.ChatIn, request: Request, key: Key = None):
    # request_id is also a replay key. Unkeyed legacy calls get a unique run.
    key = key or (str(body.request_id) if body.request_id else "unkeyed:"+str(uuid4()))
    fingerprint = digest({"method": "POST", "path": "/chat", "body": body.model_dump(mode="json")})
    id, lease, result = claim(factory(), key, fingerprint)
    if lease is None:
        return result
    try:
        return coach.run(factory(), body, id, lease)
    except Exception:
        release(factory(), id, lease)
        raise

@api.post("/chat/stream")
def chat_stream(body: sh.ChatIn, request: Request, key: Key = None):
    """Same run as POST /chat, streamed as NDJSON lines: {"type": "text"|"status", "text"} while Milo works,
    then {"type": "done", "reply", "cards"} (or {"type": "error", "detail"}). Replays return only "done"."""
    key = key or (str(body.request_id) if body.request_id else "unkeyed:"+str(uuid4()))
    fingerprint = digest({"method": "POST", "path": "/chat", "body": body.model_dump(mode="json")})
    id, lease, result = claim(factory(), key, fingerprint)
    done = lambda r: {"type": "done", **sh.ChatOut.model_validate(r).model_dump(mode="json", exclude_none=True)}
    if lease is None:
        return StreamingResponse(iter([json.dumps(done(result))+"\n"]), media_type="application/x-ndjson")
    events = queue.Queue()
    def work():
        try:
            events.put(done(coach.run(factory(), body, id, lease, emit=events.put)))
        except Exception:
            release(factory(), id, lease)
            events.put({"type": "error", "detail": "chat_failed"})
    threading.Thread(target=work, daemon=True).start()
    def lines():
        while True:
            event = events.get()
            yield json.dumps(event, default=str)+"\n"
            if event["type"] in ("done", "error"):
                return
    return StreamingResponse(lines(), media_type="application/x-ndjson")

@api.get("/chat/history", response_model=list[sh.ChatMessageOut], response_model_exclude_none=True)
def chat_history(conversation_id: UUID | None = None):
    def load(s):
        q = svc.owned(ChatMessage)
        if conversation_id:
            q = q.where(ChatMessage.conversation_id == str(conversation_id))
        return [svc.public(r) for r in s.exec(q.order_by(ChatMessage.id)).all()]
    return read(load)

@api.get("/workouts", response_model=list[sh.WorkoutOut])
def workouts(include_archived: bool = False):
    return read(lambda s: svc.list_workouts(s, include_archived))

@api.get("/workouts/{id}", response_model=sh.WorkoutOut)
def workout(id: int):
    return read(lambda s: svc.workout_out(s, svc.get(s, Workout, id)))

@api.post("/workouts/{id}/start", response_model=sh.WorkoutOut)
def start_workout(id: int, request: Request, key: Key = None):
    return write(request, key, {}, lambda s: svc.start_workout(s, id))

@api.post("/workouts/{id}/finish", response_model=sh.WorkoutOut)
def finish_workout(id: int, request: Request, key: Key = None):
    return write(request, key, {}, lambda s: svc.finish_workout(s, id))

@api.post("/exercises/{id}/sets", response_model=sh.SetOut)
def add_set(id: int, body: sh.SetIn, request: Request, key: Key = None):
    return write(request, key, body.model_dump(), lambda s: svc.add_set(s, id, body))

@api.patch("/sets/{id}", response_model=sh.SetOut)
def patch_set(id: int, body: sh.SetPatch, request: Request, key: Key = None):
    return write(request, key, body.model_dump(), lambda s: svc.patch_set(s, id, body))

@api.get("/food", response_model=sh.FoodDayOut)
def food(date: Date | None = None):
    return read(lambda s: svc.food_day(s, date))

@api.post("/food", response_model=sh.FoodOut)
def add_food(body: sh.FoodIn, request: Request, key: Key = None):
    return write(request, key, body.model_dump(mode="json"), lambda s: svc.add_food(s, body))

@api.delete("/food/{id}", response_model=sh.OK)
def delete_food(id: int, request: Request, key: Key = None):
    def delete(s):
        s.delete(svc.get(s, FoodLog, id))
        return {"ok": True}
    return write(request, key, {}, delete)

@api.get("/diet-plans", response_model=list[sh.DietOut])
def diet_plans(include_archived: bool = False):
    return read(lambda s: [svc.public(r) for r in s.exec(svc.owned(DietPlan).where(
        DietPlan.status.in_(("active", "archived") if include_archived else ("active",))).order_by(DietPlan.id.desc())).all()])

@api.get("/goals", response_model=list[sh.GoalOut])
def goals():
    return read(lambda s: [svc.goal_out(s, g) for g in s.exec(svc.owned(Goal).order_by(Goal.id)).all()])

@api.post("/goals", response_model=sh.GoalOut)
def add_goal(body: sh.GoalIn, request: Request, key: Key = None):
    return write(request, key, body.model_dump(mode="json"), lambda s: svc.add_goal(s, body))

@api.patch("/goals/{id}", response_model=sh.GoalOut)
def patch_goal(id: int, body: sh.GoalPatch, request: Request, key: Key = None):
    return write(request, key, body.model_dump(), lambda s: svc.patch_goal(s, id, body))

@api.post("/cards/{id}/apply", response_model=sh.Card, response_model_exclude_none=True)
def apply_card(id: UUID, request: Request, key: Key = None):
    return write(request, key, {}, lambda s: svc.apply_proposal(s, str(id)))

@api.get("/cards", response_model=list[sh.Card], response_model_exclude_none=True)
def pending_cards():
    return read(lambda s: [{k: v for k, v in r.card.items() if k != "replaces_plan_id"}
        for r in s.exec(svc.owned(Proposal).where(Proposal.status == "pending").order_by(Proposal.created_at, Proposal.id)).all()])

@api.get("/sessions", response_model=list[sh.SessionOut])
def session_history(workout_id: int | None = None):
    def load(s):
        q = svc.owned(WorkoutSession).where(WorkoutSession.status == "completed")
        if workout_id is not None:
            svc.get(s, Workout, workout_id)
            q = q.where(WorkoutSession.workout_id == workout_id)
        return [svc.public(r) for r in s.exec(q.order_by(WorkoutSession.finished_at.desc(), WorkoutSession.id.desc())).all()]
    return read(load)

@api.get("/sessions/{id}", response_model=sh.WorkoutOut)
def session_detail(id: int):
    def load(s):
        current = svc.get(s, WorkoutSession, id)
        return svc.workout_out(s, svc.get(s, Workout, current.workout_id), current)
    return read(load)

@api.post("/visualize/session", response_model=SessionToken)
def visualize_session():
    # Fresh per SDK preparation. Tokens are deliberately never persisted/replayed.
    return JSONResponse(content=mint_session(), headers={"Cache-Control": "no-store"})

@api.post("/scans", response_model=sh.ScanOut)
def ingest_scan(body: sh.ScanIn, request: Request, key: Key = None):
    return write(request, key, body.model_dump(mode="json"), lambda s: svc.ingest_scan(s, body, digest(body.model_dump(mode="json"))))

@api.get("/scans", response_model=list[sh.ScanOut])
def scans():
    return read(lambda s: svc.scans(s))

app.include_router(api)
