import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import select

from agent import coach
from app import services as svc, schemas as sh, db
from app.bmi import bmi_formula
from app.config import settings
from app.idempotency import claim, digest, release
from app.models import (User, FoodLog, Workout, WorkoutPlan, WorkoutSession, Mutation,
                        Goal, ChatMessage, Scan)
from app.seed import seed
from app.visualize import mint_session

WORKOUT = {"plan_name": "Three day dumbbell plan", "weeks": 6, "workouts": [
    {"day_label": day, "name": "Dumbbells "+day, "exercises": [
        {"name": "Dumbbell Bench Press", "sets": 3, "reps": 8, "kg": 15},
        {"name": "Dumbbell Row", "sets": 3, "reps": 10, "kg": 15}]}
    for day in ("MON", "WED", "FRI")]}
DIET = {"name": "Vegetarian day", "kcal": 2000, "meals": [
    {"meal": "dinner", "name": "Tofu rice bowl", "kcal": 650, "protein_g": 35, "carbs_g": 80, "fat_g": 20}]}
EGGS = {"meal": "breakfast", "name": "Two eggs and toast", "quantity": "2 eggs, 2 slices",
        "kcal": 320, "protein_g": 18, "carbs_g": 30, "fat_g": 14}

def mock_turn(monkeypatch, calls, text="Saved using the tools."):
    responses = iter([
        NS(stop_reason="tool_use", content=[NS(type="text", text="I'll save that."),
            *[NS(type="tool_use", id="t"+str(i), name=name, input=args) for i, (name, args) in enumerate(calls)]]),
        NS(stop_reason="end_turn", content=[NS(type="text", text=text)]),
    ])
    seen = []
    def respond(**kwargs):
        seen.append(copy.deepcopy(kwargs))
        return next(responses)
    monkeypatch.setattr(coach, "create_message", respond)
    return seen

def test_access_and_no_secret_echo(c, monkeypatch):
    for method, path in (("GET", "/profile"), ("GET", "/today"), ("POST", "/chat"),
                         ("GET", "/scans"), ("POST", "/visualize/session")):
        r = c.request(method, path, headers={"Authorization": "Bearer wrong"}, json={"message": "hi"})
        assert r.status_code == 401
        assert "wrong" not in r.text
    assert c.get("/health", headers={"Authorization": ""}).json() == {"ok": True}
    assert c.get("/profile", headers={"Authorization": ""}).status_code == 401
    monkeypatch.setattr(settings, "demo_api_token", "")
    assert c.get("/profile").status_code == 503

def test_ownership(c, env):
    with env[1]() as s:
        s.add(User(id=2, name="Other owner"))
        s.flush()
        f = FoodLog(owner_id=2, date="2026-10-03", meal="lunch", name="Other food", kcal=10)
        s.add(f)
        s.commit()
        id = f.id
    assert c.delete(f"/food/{id}").status_code == 404
    assert "Other food" not in c.get("/food").text
    assert c.post("/food", json=EGGS | {"owner_id": 2}).status_code == 422
    assert c.post("/profile", json={"id": 2}).status_code == 422

@pytest.mark.parametrize("height,weight", [(0, 70), (-1, 70), (180, 0), (float("nan"), 70), (180, float("inf"))])
def test_bmi_invalid(height, weight):
    with pytest.raises(ValueError):
        bmi_formula(height, weight)

def test_initial_estimates_and_incomplete(c, env):
    with env[1]() as s:
        u = svc.profile(s)
        u.height_cm = u.weight_kg = u.age = u.sex = None
        u.kcal_target = u.protein_g = u.carbs_g = u.fat_g = None
        s.add(u)
        s.commit()
    assert c.get("/today").status_code == 409
    assert c.get("/today").json()["detail"] == "profile_incomplete"
    assert c.get("/profile").json()["bmi"] is None
    r = c.post("/profile", json={"height_cm": 180, "weight_kg": 74.6, "age": 21, "sex": "male"})
    assert r.status_code == 200
    assert 2500 < r.json()["kcal_target"] < 2800
    assert r.json()["targets_source"].startswith("estimate_")
    assert c.post("/profile", json={"kcal_target": 2100}).json()["kcal_target"] == 2100
    assert c.post("/profile", json={"weight_kg": 82}).json()["kcal_target"] == 2100
    for data in ({"height_cm": 0}, {"sex": "invalid"}, {"timezone": "Nowhere/Missing"}, {"kcal_target": 0}):
        assert c.post("/profile", json=data).status_code == 422

def test_local_day_and_signed_remaining(c):
    before = c.get("/food?date=2026-10-03").json()["totals"]["kcal"]
    assert c.post("/food", json=EGGS | {"captured_at": "2026-10-03T03:59:00Z"}).status_code == 200
    assert c.get("/food?date=2026-10-02").json()["totals"]["kcal"] == 320
    assert c.get("/food?date=2026-10-03").json()["totals"]["kcal"] == before
    assert c.post("/food", json=EGGS | {"captured_at": "2026-10-03T04:00:00Z"}).status_code == 200
    assert c.get("/today?date=2026-10-03").json()["kcal"]["eaten"] == before+320
    assert c.post("/food", json=EGGS | {"date": "2026-10-01", "captured_at": "2026-10-03T04:00:00Z"}).status_code == 422
    c.post("/food", json=EGGS | {"kcal": 3000})
    assert c.get("/today").json()["kcal"]["left"] < 0
    assert c.get("/food?date=invalid").status_code == 422

def test_dst_boundaries():
    u = User(timezone="America/New_York")
    from datetime import date
    a, b = svc.boundaries(u, date(2026, 3, 8))
    assert b-a == timedelta(hours=23)
    a, b = svc.boundaries(u, date(2026, 11, 1))
    assert b-a == timedelta(hours=25)

def test_workout_story_and_proposal(c, monkeypatch):
    before = len(c.get("/workouts").json())
    mock_turn(monkeypatch, [("create_workout_plan", WORKOUT)])
    reply = c.post("/chat", json={"message": "Create a three-day dumbbell workout"}).json()
    assert reply["cards"][0]["type"] == "workout_plan"
    assert len(c.get("/workouts").json()) == before+3
    assert all(isinstance(w["id"], int) for w in c.get("/workouts").json())
    mock_turn(monkeypatch, [("create_workout_plan", WORKOUT)])
    proposed = c.post("/chat", json={"message": "Create a three-day dumbbell workout", "plan_mode": "propose"}).json()["cards"][0]
    assert proposed["status"] == "pending"
    assert len(c.get("/workouts").json()) == before+3
    assert len(c.get("/cards").json()) == 1
    assert c.post("/cards/"+proposed["id"]+"/apply").json()["status"] == "saved"
    assert c.post("/cards/"+proposed["id"]+"/apply").status_code == 200
    assert len(c.get("/workouts").json()) == before+6

def test_pending_workout_cannot_start_directly(c, monkeypatch):
    mock_turn(monkeypatch, [("create_workout_plan", WORKOUT)])
    card = c.post("/chat", json={"message": "Make a plan", "plan_mode": "propose"}).json()["cards"][0]
    assert c.post(f"/workouts/{card['data']['workouts'][0]['id']}/start").status_code == 409

def test_diet_story_and_no_consumed_food(c, monkeypatch):
    before = c.get("/today").json()["kcal"]["eaten"]
    mock_turn(monkeypatch, [("create_diet_plan", DIET)])
    r = c.post("/chat", json={"message": "Create a vegetarian diet plan"}).json()
    assert r["cards"][0]["type"] == "diet_plan"
    assert len(c.get("/diet-plans").json()) == 1
    assert c.get("/today").json()["kcal"]["eaten"] == before
    monkeypatch.setattr(coach, "create_message", lambda **kw: NS(stop_reason="end_turn", content=[NS(type="text", text="Try a tofu bowl for dinner.")]))
    c.post("/chat", json={"message": "What should I eat for dinner?"})
    assert c.get("/today").json()["kcal"]["eaten"] == before

def test_eggs_chat_key_replay(c, monkeypatch):
    before = c.get("/today").json()["kcal"]["eaten"]
    seen = mock_turn(monkeypatch, [("log_food", EGGS)], "Logged an estimated 320 kcal.")
    body = {"message": "I ate two eggs and toast"}
    headers = {"Idempotency-Key": "eggs-story"}
    first = c.post("/chat", json=body, headers=headers)
    assert first.status_code == 200
    assert c.post("/chat", json=body, headers=headers).json() == first.json()
    assert c.get("/today").json()["kcal"]["eaten"] == before+320
    f = c.get("/food").json()["meals"]["breakfast"][-1]
    assert f["nutrition_provenance"] == "coach_estimate" and f["is_estimate"]
    assert len(seen) == 2
    results = seen[1]["messages"][-1]["content"]
    assert results[0]["tool_use_id"] == "t0"
    assert c.post("/chat", json={"message": "Different meal"}, headers=headers).status_code == 409

def test_food_keys_concurrency_and_conflicts(c):
    def write():
        return c.post("/food", json=EGGS, headers={"Idempotency-Key": "concurrent-food"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda _: write(), range(4)))
    assert all(r.status_code == 200 for r in rows)
    assert len({r.json()["id"] for r in rows}) == 1
    assert c.post("/food", json=EGGS | {"kcal": 321}, headers={"Idempotency-Key": "concurrent-food"}).status_code == 409
    # A key is scoped to the owner, not reusable for a different route.
    assert c.post("/goals", json={"title": "Goal", "kind": "habit", "target_value": 3},
                  headers={"Idempotency-Key": "concurrent-food"}).status_code == 409

def test_concurrent_chat_claim(c, monkeypatch):
    entered, proceed = threading.Event(), threading.Event()
    calls = []
    def respond(**kwargs):
        calls.append(1)
        entered.set()
        assert proceed.wait(10)
        return NS(stop_reason="end_turn", content=[NS(type="text", text="Hello.")])
    monkeypatch.setattr(coach, "create_message", respond)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(c.post, "/chat", json={"message": "Hi"}, headers={"Idempotency-Key": "chat-concurrent"})
        assert entered.wait(10)
        assert c.post("/chat", json={"message": "Hi"}, headers={"Idempotency-Key": "chat-concurrent"}).status_code == 409
        proceed.set()
        assert pending.result().status_code == 200
    assert len(calls) == 1

def test_repeated_sessions_and_frequency_goal(c, monkeypatch):
    w = c.get("/workouts").json()[-1]
    before = c.get("/today").json()["week"]["sessions_done"]
    first = c.post(f"/workouts/{w['id']}/start").json()
    sid, setid = first["session_id"], first["exercises"][0]["sets"][0]["id"]
    mock_turn(monkeypatch, [("log_workout_set", {"exercise_name": "Bench Press", "kg": 75, "reps": 8, "session_id": sid})])
    card = c.post("/chat", json={"message": "I benched 75 kg for 8"}).json()["cards"][0]
    assert card["type"] == "set_logged" and card["data"]["session_id"] == sid
    c.post(f"/workouts/{w['id']}/finish")
    second = c.post(f"/workouts/{w['id']}/start").json()
    assert second["session_id"] != sid
    assert second["exercises"][0]["sets"][0]["id"] != setid
    assert not second["exercises"][0]["sets"][0]["done"]
    assert c.get(f"/sessions/{sid}").json()["exercises"][0]["sets"][0]["done"]
    # Finished sessions stay editable (fixing a workout after Finish).
    assert c.patch(f"/sets/{setid}", json={"reps": 9}).json()["reps"] == 9
    assert c.get(f"/sessions/{sid}").json()["exercises"][0]["sets"][0]["reps"] == 9
    c.post(f"/workouts/{w['id']}/finish")
    mock_turn(monkeypatch, [("set_goal", {"title": "Train three times weekly", "kind": "habit", "unit": "sessions", "target_value": 3})])
    c.post("/chat", json={"message": "Set a training frequency goal"})
    goal = c.get("/goals").json()[-1]
    assert goal["progress_source"] == "weekly_sessions" and goal["current_value"] == before+2
    assert c.patch(f"/goals/{goal['id']}", json={"current_value": 99}).status_code == 409
    assert c.get("/today?date=2026-09-20").json()["week"]["sessions_done"] == 0
    assert len(c.get("/sessions").json()) == before+2

def test_failed_tool_save_no_success_card(c, monkeypatch, env):
    def fail(s, body, mode):
        s.add(WorkoutPlan(owner_id=1, name="Must roll back", weeks=1))
        s.flush()
        raise SQLAlchemyError("secret database details")
    monkeypatch.setattr(svc, "create_plan", fail)
    seen = mock_turn(monkeypatch, [("create_workout_plan", WORKOUT)], "It is saved!")
    r = c.post("/chat", json={"message": "Create a plan"}).json()
    assert r["cards"] == [] and "No actions were saved" in r["reply"]
    assert seen[1]["messages"][-1]["content"][0]["is_error"]
    assert "secret" not in str(r)
    with env[1]() as s:
        assert s.exec(select(WorkoutPlan).where(WorkoutPlan.name == "Must roll back")).first() is None

def test_duplicate_tools_and_multiple_results(c, monkeypatch):
    before = c.get("/today").json()["kcal"]["eaten"]
    seen = mock_turn(monkeypatch, [("log_food", EGGS), ("log_food", EGGS), ("get_today", {})])
    r = c.post("/chat", json={"message": "Log eggs"}).json()
    assert len(r["cards"]) == 1
    assert c.get("/today").json()["kcal"]["eaten"] == before+320
    assert [r["tool_use_id"] for r in seen[1]["messages"][-1]["content"]] == ["t0", "t1", "t2"]

def test_malformed_unknown_and_bounded_tools(c, monkeypatch):
    seen = mock_turn(monkeypatch, [("arbitrary_sql", {"sql": "DROP TABLE user"}), ("log_food", EGGS | {"kcal": -1})])
    r = c.post("/chat", json={"message": "Log food"}).json()
    assert r["cards"] == []
    assert all(x["is_error"] for x in seen[1]["messages"][-1]["content"])
    calls = []
    def forever(**kwargs):
        calls.append(1)
        return NS(stop_reason="tool_use", content=[NS(type="tool_use", id=f"t{len(calls)}_{i}", name="get_today", input={}) for i in range(5)])
    monkeypatch.setattr(coach, "create_message", forever)
    assert c.post("/chat", json={"message": "Loop"}).status_code == 200
    assert len(calls) <= coach.MAX_ITERATIONS
    assert c.get("/today").status_code == 200

def test_partial_provider_failure_replay(c, monkeypatch):
    calls = []
    def respond(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="log_food", input=EGGS)])
        raise RuntimeError("provider secret")
    monkeypatch.setattr(coach, "create_message", respond)
    body = {"message": "Log eggs"}
    headers = {"Idempotency-Key": "partial"}
    first = c.post("/chat", json=body, headers=headers).json()
    assert len(first["cards"]) == 1 and "Some actions were saved" in first["reply"]
    assert c.post("/chat", json=body, headers=headers).json() == first
    assert len(calls) == 2

def test_checkpoint_resume_after_crash(env, monkeypatch):
    factory = env[1]
    body = sh.ChatIn(message="Log eggs")
    fingerprint = digest({"body": body.model_dump(mode="json")})
    id, lease, _ = claim(factory, "resume", fingerprint)
    calls = []
    def crash(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="log_food", input=EGGS)])
        raise KeyboardInterrupt()
    monkeypatch.setattr(coach, "create_message", crash)
    with pytest.raises(KeyboardInterrupt):
        coach.run(factory, body, id, lease)
    release(factory, id, lease)
    id2, lease2, _ = claim(factory, "resume", fingerprint)
    monkeypatch.setattr(coach, "create_message", lambda **kw: NS(stop_reason="end_turn", content=[NS(type="text", text="The saved food is available.")]))
    result = coach.run(factory, body, id2, lease2)
    assert len(result["cards"]) == 1
    with factory() as s:
        assert len(s.exec(select(FoodLog).where(FoodLog.name == EGGS["name"])).all()) == 1
        assert len(s.exec(select(ChatMessage).where(ChatMessage.request_ref == id, ChatMessage.role == "user")).all()) == 1

def scan_body():
    return {"client_scan_id": str(uuid4()), "captured_at": "2026-10-03T12:00:00Z",
            "metrics": [{"identifier": "waist_circumference", "value": 82, "unit": "cm"}],
            "source": "manual", "source_metadata": {"mapping_version": "illustrative-v1"}}

def test_scan_consent_dedup_and_ai_context(c, env):
    body = scan_body()
    assert c.post("/scans", json=body).status_code == 403
    assert c.get("/profile").json()["scan_ai_sharing_consent"] is False
    c.post("/profile", json={"scan_storage_consent": True})
    first = c.post("/scans", json=body)
    assert first.status_code == 200 and first.json()["verification"] == "manual"
    assert c.post("/scans", json=body).json()["id"] == first.json()["id"]
    conflicting = copy.deepcopy(body)
    conflicting["metrics"][0]["value"] = 83
    assert c.post("/scans", json=conflicting).status_code == 409
    with env[1]() as s:
        assert json.loads(coach.context_block(s).split("\n", 1)[1])["permitted_scan_summaries"] == []
    c.post("/profile", json={"scan_ai_sharing_consent": True})
    with env[1]() as s:
        assert len(json.loads(coach.context_block(s).split("\n", 1)[1])["permitted_scan_summaries"]) == 1
    c.post("/profile", json={"scan_storage_consent": False})
    assert c.get("/scans").json() == []
    assert c.get("/today").json()["latest_scan"] is None

def test_invalid_scan_summary(c):
    c.post("/profile", json={"scan_storage_consent": True})
    for metric in ({"identifier": "bad-field", "value": 3, "unit": "cm"},
                   {"identifier": "waist", "value": -3, "unit": "cm"},
                   {"identifier": "body_fat", "value": 101, "unit": "percent"},
                   {"identifier": "waist", "value": 3, "unit": "meters"}):
        assert c.post("/scans", json=scan_body() | {"metrics": [metric]}).status_code == 422
    assert c.post("/scans", json=scan_body() | {"source_metadata": {"frames": "camera-data"}}).status_code == 422
    assert c.post("/scans", json=scan_body() | {"captured_at": "2026-10-03T12:00:00"}).status_code == 422

def test_visualize_request_and_no_cache(c, monkeypatch):
    real_client = httpx.Client
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"session_token": "token-"+str(len(seen)), "expires_at": "2030-01-01T00:00:00Z"})
    monkeypatch.setattr(settings, "visualize_secret_key", "server-secret")
    monkeypatch.setattr(settings, "visualize_host_user_ref", "milo_demo_1")
    monkeypatch.setattr(settings, "visualize_api_base_url", "https://api.visualizeme.ai")
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    a, b = c.post("/visualize/session"), c.post("/visualize/session")
    assert a.json()["session_token"] != b.json()["session_token"]
    assert a.headers["cache-control"] == "no-store"
    assert seen[0].url == "https://api.visualizeme.ai/v1/sessions"
    assert seen[0].headers["authorization"] == "Bearer server-secret"
    assert json.loads(seen[0].content) == {"host_user_ref": "milo_demo_1"}
    assert "server-secret" not in a.text

@pytest.mark.parametrize("kind,status", [("timeout", 504), ("upstream", 502), ("malformed", 502), ("expired", 502)])
def test_visualize_sanitized_errors(c, monkeypatch, kind, status):
    real_client = httpx.Client
    def respond(request):
        if kind == "timeout":
            raise httpx.ReadTimeout("secret upstream data")
        if kind == "upstream":
            return httpx.Response(401, text="secret upstream data")
        if kind == "expired":
            return httpx.Response(200, json={"session_token": "x", "expires_at": "2020-01-01T00:00:00Z"})
        return httpx.Response(200, json={"secret": "provider data"})
    monkeypatch.setattr(settings, "visualize_secret_key", "server-secret")
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    r = c.post("/visualize/session")
    assert r.status_code == status and "secret" not in r.text

def test_seed_non_destructive_and_restart(env):
    c, factory, engine = env
    f = c.post("/food", json=EGGS).json()
    c.post("/profile", json={"weight_kg": 80, "kcal_target": 2222})
    seed(factory=factory)
    seed(factory=factory, fresh=True)
    assert c.get("/profile").json()["weight_kg"] == 80
    assert c.get("/profile").json()["kcal_target"] == 2222
    assert c.get("/today").json()["kcal"]["eaten"] == 1940
    # Dispose all connections and reopen the persisted test database.
    engine.dispose()
    with factory() as s:
        assert svc.get(s, FoodLog, f["id"]).name == EGGS["name"]
        assert len(s.exec(svc.owned(Workout)).all()) == 4

def test_production_has_no_sqlite_fallback(monkeypatch):
    db.get_engine.cache_clear()
    for url in ("", "sqlite:///fitcoach.db", "postgresql+psycopg://user:password@localhost/db"):
        monkeypatch.setattr(settings, "database_url", url)
        with pytest.raises(RuntimeError):
            db.get_engine()
    db.get_engine.cache_clear()


def test_coach_edits_finished_session(c, monkeypatch):
    w = c.get("/workouts").json()[-1]
    sid = c.post(f"/workouts/{w['id']}/start").json()["session_id"]
    c.post(f"/workouts/{w['id']}/finish")
    # Editing a finished session names it explicitly.
    mock_turn(monkeypatch, [("get_recent_sessions", {}),
                            ("log_workout_set", {"exercise_name": "Bench Press", "kg": 55, "reps": 8, "set_index": 0,
                                                 "session_id": sid})])
    card = c.post("/chat", json={"message": "Fix my bench: first set was 55 kg x 8"}).json()["cards"][0]
    assert card["type"] == "set_logged" and card["data"]["session_id"] == sid
    first = c.get(f"/sessions/{sid}").json()["exercises"][0]["sets"][0]
    assert (first["kg"], first["reps"], first["done"]) == (55, 8, True)


def test_meal_photo_reaches_claude_but_is_not_stored(c, monkeypatch):
    img = "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAg="  # tiny fake JPEG header, base64
    seen = mock_turn(monkeypatch, [("log_food", {"meal": "lunch", "name": "Salmon bowl", "quantity": "1 bowl",
                                                 "kcal": 650, "protein_g": 40, "carbs_g": 60, "fat_g": 22})])
    r = c.post("/chat", json={"message": "[Photo] Log what I ate", "image_jpeg_base64": img}).json()
    assert r["cards"][0]["type"] == "food_logged"
    first_call = seen[0]["messages"][-1]["content"]
    assert first_call[0]["type"] == "image" and first_call[0]["source"]["data"] == img
    history = c.get("/chat/history").json()
    assert all(img not in str(m) for m in history)


def test_chat_stream_sends_text_status_and_done(c, monkeypatch):
    turns = iter([
        NS(stop_reason="tool_use", content=[NS(type="text", text="Let me log that."),
            NS(type="tool_use", id="t0", name="log_food", input={"meal": "snack", "name": "Apple", "quantity": "1",
                                                                  "kcal": 95, "protein_g": 0, "carbs_g": 25, "fat_g": 0})]),
        NS(stop_reason="end_turn", content=[NS(type="text", text="Logged an apple.")]),
    ])
    def respond(on_text=None, **kwargs):
        turn = next(turns)
        for b in turn.content:
            if b.type == "text" and on_text:
                for word in b.text.split(" "):
                    on_text(word + " ")
        return turn
    monkeypatch.setattr(coach, "create_message", respond)
    r = c.post("/chat/stream", json={"message": "I ate an apple"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    events = [json.loads(line) for line in r.text.splitlines() if line]
    kinds = [e["type"] for e in events]
    assert kinds.count("text") >= 4 and "status" in kinds and kinds[-1] == "done"
    assert kinds.index("status") < len(kinds) - 1 and events[kinds.index("status")]["text"] == "Logging food…"
    assert events[-1]["reply"] == "Logged an apple." and events[-1]["cards"][0]["type"] == "food_logged"
    c.headers.pop("Authorization", None)
    assert c.post("/chat/stream", json={"message": "hi"}).status_code == 401


def test_coach_starts_workout_before_logging(c, monkeypatch):
    w = c.get("/workouts").json()[-1]
    assert w["status"] != "active"
    mock_turn(monkeypatch, [("start_workout", {"workout_id": w["id"]}),
                            ("log_workout_set", {"exercise_name": "Bench Press", "kg": 70, "reps": 10})])
    card = c.post("/chat", json={"message": "Just did bench 70 kg for 10"}).json()["cards"][0]
    assert card["type"] == "set_logged" and card["data"]["kg"] == 70
    now = c.get(f"/workouts/{w['id']}").json()
    assert now["status"] == "active" and now["exercises"][0]["sets"][0]["reps"] == 10
    # Without a started workout, logging alone is refused with guidance.
    c.post(f"/workouts/{w['id']}/finish")
    mock_turn(monkeypatch, [("log_workout_set", {"exercise_name": "Bench Press", "kg": 70, "reps": 10})])
    assert c.post("/chat", json={"message": "bench 70x10"}).json()["cards"] == []
