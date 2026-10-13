"""Transactional domain operations shared by REST and Claude; callers own commits."""
from datetime import datetime, time, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import select

from app.bmi import bmi_formula, category
from app.config import settings
from app.models import (User, WorkoutPlan, Workout, Exercise, WorkoutSession,
                        WorkoutSet, FoodLog, DietPlan, Goal, Proposal, Scan, utcnow)

MEALS = ("breakfast", "lunch", "dinner", "snack")


def owned(model):
    return select(model).where(model.owner_id == settings.demo_user_id)


def get(s, model, id):
    row = s.exec(owned(model).where(model.id == id)).first()
    if row is None:
        raise HTTPException(404, "Resource not found")
    return row


def profile(s, required=False):
    u = s.get(User, settings.demo_user_id)
    if u is None and required:
        raise HTTPException(409, "profile_incomplete")
    return u or User(id=settings.demo_user_id, timezone=settings.app_timezone)


def guard_owner(s):
    # Cross-process serialization only for short domain transactions.
    if s.get_bind().dialect.name == "postgresql":
        s.execute(text("SELECT pg_advisory_xact_lock(784103, :owner)"),
                  {"owner": settings.demo_user_id})


def public(row):
    data = row.model_dump(mode="json", exclude={"owner_id", "seed_key", "payload_hash", "active_slot"})
    if isinstance(row, User):
        value = bmi_formula(row.height_cm, row.weight_kg) if row.height_cm is not None and row.weight_kg is not None else None
        data["bmi"], data["bmi_category"] = value, category(value) if value is not None else None
    return data


def local_day(u, instant=None):
    return (instant or utcnow()).astimezone(ZoneInfo(u.timezone)).date()


def boundaries(u, day):
    z = ZoneInfo(u.timezone)
    return (datetime.combine(day, time.min, z).astimezone(timezone.utc),
            datetime.combine(day + timedelta(days=1), time.min, z).astimezone(timezone.utc))


def aware(value):
    # SQLite is an explicit test adapter and does not preserve tzinfo.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def save_profile(s, body):
    u = profile(s)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(u, k, v)
    if u.height_cm is not None and u.weight_kg is not None:
        u.bmi = bmi_formula(u.height_cm, u.weight_kg)
        u.bmi_category = category(u.bmi)
    else:
        u.bmi = u.bmi_category = None
    targets = ("kcal_target", "protein_g", "carbs_g", "fat_g")
    supplied = any(k in body.model_fields_set and getattr(body, k) is not None for k in targets)
    # Initial estimate only. Weight edits never recalculate existing targets.
    if not supplied and all(getattr(u, k) is None for k in targets) and all(
            getattr(u, k) is not None for k in ("height_cm", "weight_kg", "age", "sex")):
        bmr = 10*u.weight_kg + 6.25*u.height_cm - 5*u.age + (5 if u.sex == "male" else -161)
        u.kcal_target = max(1, round(bmr*1.55))
        u.protein_g = max(1, round(2.2*u.weight_kg))
        u.fat_g = max(1, round(u.kcal_target*0.25/9))
        u.carbs_g = max(1, round((u.kcal_target-u.protein_g*4-u.fat_g*9)/4))
        u.targets_source = "estimate_mifflin_st_jeor_activity_1.55"
    elif supplied:
        u.targets_source = "explicit_override"
    s.add(u)
    s.flush()
    return public(u)


def latest_session(s, workout_id):
    return s.exec(owned(WorkoutSession).where(WorkoutSession.workout_id == workout_id)
                  .order_by(WorkoutSession.id.desc())).first()


def exercises(s, workout_id):
    return s.exec(owned(Exercise).where(Exercise.workout_id == workout_id)
                  .order_by(Exercise.position, Exercise.id)).all()


def session_sets(s, sid, eid):
    return s.exec(owned(WorkoutSet).where(WorkoutSet.session_id == sid, WorkoutSet.exercise_id == eid)
                  .order_by(WorkoutSet.set_index, WorkoutSet.id)).all()


def prepare_session(s, w):
    row = WorkoutSession(owner_id=settings.demo_user_id, workout_id=w.id)
    s.add(row)
    s.flush()
    for ex in exercises(s, w.id):
        for i in range(ex.target_sets):
            s.add(WorkoutSet(owner_id=settings.demo_user_id, session_id=row.id,
                             exercise_id=ex.id, set_index=i, kg=ex.target_kg or 0, reps=ex.target_reps))
    s.flush()
    return row


def workout_out(s, w, session=None):
    current = session or latest_session(s, w.id)
    data = public(w) | {
        "status": {"prepared": "planned", "active": "active", "completed": "done"}.get(current.status if current else "", "planned"),
        "started_at": aware(current.started_at).isoformat() if current and current.started_at else None,
        "finished_at": aware(current.finished_at).isoformat() if current and current.finished_at else None,
        "session_id": current.id if current else None,
        "exercises": [],
    }
    for ex in exercises(s, w.id):
        data["exercises"].append(public(ex) | {"sets": [public(x) for x in session_sets(s, current.id, ex.id)] if current else []})
    return data


def list_workouts(s, include_archived=False):
    query = owned(Workout).join(WorkoutPlan, Workout.plan_id == WorkoutPlan.id).where(
        WorkoutPlan.owner_id == settings.demo_user_id,
        WorkoutPlan.status.in_(("active", "archived") if include_archived else ("active",)))
    return [workout_out(s, w) for w in s.exec(query.order_by(Workout.id)).all()]


def start_workout(s, id):
    w = get(s, Workout, id)
    plan = get(s, WorkoutPlan, w.plan_id)
    if plan.status != "active":
        raise HTTPException(409, "Plan is not active")
    active = s.exec(owned(WorkoutSession).where(WorkoutSession.status == "active")).first()
    if active:
        if active.workout_id != id:
            raise HTTPException(409, "Finish the active workout first")
        return workout_out(s, w, active)
    current = latest_session(s, id)
    if current is None or current.status == "completed":
        current = prepare_session(s, w)
    current.status, current.started_at, current.active_slot = "active", utcnow(), 1
    s.add(current)
    s.flush()
    return workout_out(s, w, current)


def finish_workout(s, id):
    w = get(s, Workout, id)
    current = latest_session(s, id)
    if current and current.status == "completed":
        return workout_out(s, w, current)
    if current is None or current.status != "active":
        raise HTTPException(409, "Start this workout first")
    current.status, current.finished_at, current.active_slot = "completed", utcnow(), None
    s.add(current)
    s.flush()
    return workout_out(s, w, current)


def add_set(s, id, body):
    ex = get(s, Exercise, id)
    session = latest_session(s, ex.workout_id)
    if session is None or session.status not in ("active", "completed"):  # finished sessions stay editable
        raise HTTPException(409, "Start this workout before adding sets")
    rows = session_sets(s, session.id, id)
    row = WorkoutSet(owner_id=settings.demo_user_id, exercise_id=id, session_id=session.id,
                     set_index=max((r.set_index for r in rows), default=-1)+1, **body.model_dump())
    s.add(row)
    s.flush()
    return public(row)


def patch_set(s, id, body):
    row = get(s, WorkoutSet, id)
    session = get(s, WorkoutSession, row.session_id)
    if session.status not in ("active", "completed"):  # finished sessions stay editable
        raise HTTPException(409, "Start this workout before editing sets")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(row, k, v)
    s.add(row)
    s.flush()
    return public(row)


def log_set(s, body):
    # Explicit session_id may be a finished session (fixing a workout after Finish).
    session = (get(s, WorkoutSession, body.session_id) if body.session_id else
               s.exec(owned(WorkoutSession).where(WorkoutSession.status == "active")).first())
    if session is None:
        raise HTTPException(409, "No active session: call start_workout for the intended workout, or pass session_id to edit a finished one")
    if session.status not in ("active", "completed"):
        raise HTTPException(409, "Start the intended workout first")
    matches = [e for e in exercises(s, session.workout_id) if
               (e.id == body.exercise_id if body.exercise_id else e.name.casefold() == body.exercise_name.casefold())]
    if len(matches) != 1:
        raise HTTPException(409, "Ask which exercise in that session")
    ex = matches[0]
    rows = session_sets(s, session.id, ex.id)
    target = next((r for r in rows if r.set_index == body.set_index), None) if body.set_index is not None else next((r for r in rows if not r.done), None)
    if target is None:
        target = WorkoutSet(owner_id=settings.demo_user_id, exercise_id=ex.id, session_id=session.id,
                            set_index=body.set_index if body.set_index is not None else max((r.set_index for r in rows), default=-1)+1)
    target.kg, target.reps, target.done, target.is_warmup = body.kg, body.reps, True, body.is_warmup
    s.add(target)
    s.flush()
    data = {"exercise": ex.name, "kg": target.kg, "reps": target.reps, "id": target.id, "session_id": session.id}
    return data, {"type": "set_logged", "data": data}


def add_food(s, body, coach=False):
    u = profile(s, required=True)
    instant = body.captured_at
    if instant is None:
        instant = (datetime.combine(body.date, time(12), ZoneInfo(u.timezone)).astimezone(timezone.utc)
                   if body.date else utcnow())
    day = local_day(u, instant)
    if body.date and body.date != day:
        raise HTTPException(422, "date conflicts with captured_at in the profile timezone")
    data = body.model_dump(exclude={"date", "captured_at"})
    if coach:
        data["source"] = "coach"
        if data["nutrition_provenance"] != "nutrition_label":
            data["nutrition_provenance"], data["is_estimate"] = "coach_estimate", True
    if data["nutrition_provenance"] == "nutrition_label":
        data["is_estimate"] = False
    row = FoodLog(owner_id=settings.demo_user_id, date=day.isoformat(), captured_at=instant.astimezone(timezone.utc), **data)
    s.add(row)
    s.flush()
    return public(row)


def food_items(s, day):
    u = profile(s)
    start, end = boundaries(u, day)
    return s.exec(owned(FoodLog).where(FoodLog.captured_at >= start, FoodLog.captured_at < end)
                  .order_by(FoodLog.captured_at, FoodLog.id)).all()


def food_totals(s, day):
    items = food_items(s, day)
    return {k: round(sum(getattr(f, k) for f in items)) for k in ("kcal", "protein_g", "carbs_g", "fat_g")}


def food_day(s, day=None):
    day = day or local_day(profile(s))
    items = food_items(s, day)
    return {"date": day.isoformat(), "meals": {m: [public(f) for f in items if f.meal == m] for m in MEALS},
            "totals": food_totals(s, day)}


def week_sessions(s, day):
    u = profile(s)
    monday = day - timedelta(days=day.weekday())
    start = boundaries(u, monday)[0]
    end = boundaries(u, monday+timedelta(days=7))[0]
    return s.exec(owned(WorkoutSession).where(WorkoutSession.status == "completed",
                  WorkoutSession.finished_at >= start, WorkoutSession.finished_at < end)
                  .order_by(WorkoutSession.finished_at, WorkoutSession.id)).all()


def goal_out(s, g, day=None):
    data = public(g)
    day = day or local_day(profile(s))
    if g.progress_source == "weekly_sessions":
        data["current_value"] = len(week_sessions(s, day))
    elif g.progress_source == "daily_protein":
        data["current_value"] = food_totals(s, day)["protein_g"]
    return data


def add_goal(s, body):
    profile(s, required=True)
    data = body.model_dump()
    data["current_value"] = body.current_value if body.current_value is not None else body.start_value
    if body.kind == "habit" and body.unit == "sessions":
        data["progress_source"] = "weekly_sessions"
    row = Goal(owner_id=settings.demo_user_id, **data)
    s.add(row)
    s.flush()
    return goal_out(s, row)


def patch_goal(s, id, body):
    row = get(s, Goal, id)
    if body.current_value is not None and row.progress_source != "explicit":
        raise HTTPException(409, "This goal's progress is calculated from logs")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(row, k, v)
    s.add(row)
    s.flush()
    return goal_out(s, row)


def create_plan(s, body, mode="save"):
    profile(s, required=True)
    row = WorkoutPlan(owner_id=settings.demo_user_id, name=body.plan_name, weeks=body.weeks,
                      status="pending" if mode == "propose" else "active")
    s.add(row)
    s.flush()
    days = []
    for spec in body.workouts:
        w = Workout(owner_id=settings.demo_user_id, plan_id=row.id, name=spec.name, day_label=spec.day_label, notes=spec.notes)
        s.add(w)
        s.flush()
        for pos, e in enumerate(spec.exercises):
            s.add(Exercise(owner_id=settings.demo_user_id, workout_id=w.id, name=e.name, position=pos,
                           target_sets=e.sets, target_reps=e.reps, target_kg=e.kg))
        s.flush()
        prepare_session(s, w)
        days.append({"id": w.id, "name": w.name, "day_label": w.day_label, "summary": " · ".join(e.name for e in spec.exercises)})
    data = {"plan_name": row.name, "weeks": row.weeks, "workouts": days, "plan_id": row.id}
    return plan_card(s, "workout_plan", row, data, mode, body.replaces_plan_id)


def create_diet(s, body, mode="save"):
    profile(s, required=True)
    row = DietPlan(owner_id=settings.demo_user_id, name=body.name, kcal=body.kcal,
                   meals=[m.model_dump() for m in body.meals], status="pending" if mode == "propose" else "active")
    s.add(row)
    s.flush()
    return plan_card(s, "diet_plan", row, public(row), mode, body.replaces_plan_id)


def plan_card(s, kind, row, data, mode, replaces):
    model = WorkoutPlan if kind == "workout_plan" else DietPlan
    if replaces is not None:
        old = get(s, model, replaces)
        if old.status != "active":
            raise HTTPException(409, "Replacement plan must be active")
    card = {"type": kind, "data": data, "status": "pending" if mode == "propose" else "saved"}
    if mode == "propose":
        id = str(uuid4())
        card |= {"id": id, "action": {"method": "POST", "path": "/cards/"+id+"/apply"}}
        s.add(Proposal(id=id, owner_id=settings.demo_user_id, kind=kind, resource_id=row.id,
                       card=card | {"replaces_plan_id": replaces}))
    elif replaces is not None:
        archive_plan(s, old)
    s.flush()
    return data, card


def archive_plan(s, row):
    if isinstance(row, WorkoutPlan):
        active = s.exec(owned(WorkoutSession).join(Workout, WorkoutSession.workout_id == Workout.id)
                        .where(Workout.owner_id == settings.demo_user_id, Workout.plan_id == row.id,
                               WorkoutSession.status == "active")).first()
        if active:
            raise HTTPException(409, "Finish the active workout before replacing its plan")
    row.status = "archived"
    s.add(row)


def apply_proposal(s, id):
    proposal = get(s, Proposal, id)
    if proposal.status == "applied":
        return proposal.card
    model = WorkoutPlan if proposal.kind == "workout_plan" else DietPlan
    row = get(s, model, proposal.resource_id)
    if row.status != "pending":
        raise HTTPException(409, "Proposal is not pending")
    replaces = proposal.card.get("replaces_plan_id")
    if replaces:
        old = get(s, model, replaces)
        if old.status != "active":
            raise HTTPException(409, "Replacement plan changed; create a new proposal")
        archive_plan(s, old)
    row.status = "active"
    proposal.status = "applied"
    proposal.card = {k: v for k, v in proposal.card.items() if k != "replaces_plan_id"} | {"status": "saved"}
    if proposal.kind == "diet_plan":
        proposal.card = proposal.card | {"data": public(row)}
    s.add(row)
    s.add(proposal)
    s.flush()
    return proposal.card


def ingest_scan(s, body, fingerprint):
    u = profile(s, required=True)
    if not u.scan_storage_consent:
        raise HTTPException(403, "scan_storage_consent_required")
    existing = s.exec(owned(Scan).where(Scan.client_scan_id == str(body.client_scan_id))).first()
    if existing:
        if existing.payload_hash != fingerprint:
            raise HTTPException(409, "client_scan_id payload conflict")
        return public(existing)
    if body.captured_at > utcnow()+timedelta(minutes=5):
        raise HTTPException(422, "captured_at cannot be in the future")
    row = Scan(owner_id=settings.demo_user_id, client_scan_id=str(body.client_scan_id),
               payload_hash=fingerprint, captured_at=body.captured_at.astimezone(timezone.utc),
               metrics=[m.model_dump() for m in body.metrics], source=body.source,
               source_metadata=body.source_metadata,
               verification="client-reported" if body.source == "visualize_sdk" else body.source)
    s.add(row)
    s.flush()
    return public(row)


def scans(s, ai=False):
    u = profile(s)
    if not u.scan_storage_consent or (ai and not u.scan_ai_sharing_consent):
        return []
    return [public(r) for r in s.exec(owned(Scan).order_by(Scan.captured_at.desc(), Scan.id.desc()).limit(20)).all()]
