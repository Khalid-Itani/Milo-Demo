"""Explicit schema allowlist; REST and tools use the same domain services."""
from app import schemas as sh, services as svc
from app.models import DietPlan, Goal, Workout, WorkoutSession
from app.today import build_today

SCHEMAS = {
    "create_workout_plan": sh.WorkoutPlanIn,
    "start_workout": sh.StartWorkoutIn,
    "log_workout_set": sh.LogSetIn,
    "create_diet_plan": sh.DietPlanIn,
    "log_food": sh.FoodIn,
    "set_goal": sh.GoalIn,
    "get_today": sh.EmptyIn,
    "get_profile": sh.EmptyIn,
    "get_saved_plans": sh.EmptyIn,
    "get_goals": sh.EmptyIn,
    "get_recent_sessions": sh.EmptyIn,
}
READ_TOOLS = frozenset({"get_today", "get_profile", "get_saved_plans", "get_goals", "get_recent_sessions"})
DESCRIPTIONS = {
    "create_workout_plan": "Save a workout plan with planned exercises. Respect equipment, experience and available days. No completed sessions are invented. In proposal mode the plan remains pending until the user applies its card. Supply replaces_plan_id only when explicitly revising a saved plan.",
    "start_workout": "Start a saved workout (workout_id from the saved workouts in context) so sets can be logged. Use when the user reports doing or logging sets and no session is active; pick the workout matching what they describe and ask if unclear. Fails if another workout is active.",
    "log_workout_set": "Log or edit an actually completed set. Without session_id it targets the active session; if none is active, call start_workout first. To edit a finished session, call get_recent_sessions and pass its session_id. Use exact exercise name or an explicit exercise_id. Passing an existing set_index overwrites that set's kg/reps; a new set_index adds a set. For multiple identical sets use distinct set_index values; omission means the first unfinished set.",
    "create_diet_plan": "Save suggested meals as a diet plan. This does not log consumed food or change eaten totals. Respect allergies and preferences. Proposal mode requires explicit application. Revisions preserve the earlier plan.",
    "log_food": "Log only food the user explicitly reports eating or explicitly requests logging. Never log a dinner suggestion as consumed. Mark free-text nutrition as coach_estimate; use nutrition_label only when the user provides the label values. Do not invent labels. Quantities and nutrition numbers refer to the entire consumed portion.",
    "set_goal": "Save an explicitly requested goal. Do not invent the starting strength or measurements; ask if essential. Habit goals in sessions use actual weekly completed sessions. Explicit strength/body progress needs a user update. A nutrition goal can opt into daily_protein progress.",
    "get_today": "Read authoritative local-day totals and dated weekly completed session counts. Never recompute totals in the model. A profile_incomplete result means targets are missing. Ask one focused question.",
    "get_profile": "Read the saved profile, preferences, allergies, equipment and separate scan consents. Do not infer missing personal facts. This tool exposes no secrets.",
    "get_saved_plans": "Read active workout and diet plans. Meals here are suggestions, not consumed food. Pending proposals do not appear as active plans.",
    "get_recent_sessions": "Read the 5 most recent active or finished workout sessions with session_id, exercises (with exercise_id) and each set's set_index, kg, reps and done. Use before editing a finished session.",
    "get_goals": "Read saved goals and authoritative progress calculated from real logs where configured. Do not infer 1RM or body measurements.",
}
TOOLS = [{"name": name, "description": DESCRIPTIONS[name], "input_schema": schema.model_json_schema()}
         for name, schema in SCHEMAS.items()]

def execute(s, name, args, plan_mode="save"):
    if name not in SCHEMAS:
        raise ValueError("Unknown tool")
    body = SCHEMAS[name].model_validate(args)
    if name == "create_workout_plan":
        return svc.create_plan(s, body, plan_mode)
    if name == "create_diet_plan":
        return svc.create_diet(s, body, plan_mode)
    if name == "start_workout":
        w = svc.start_workout(s, body.workout_id)
        return {"session_id": w["session_id"], "workout_id": w["id"], "name": w["name"], "status": w["status"]}, None
    if name == "log_workout_set":
        return svc.log_set(s, body)
    if name == "log_food":
        data = svc.add_food(s, body, coach=True)
        card = {"type": "food_logged", "data": data, "status": "saved"}
        return data, card
    if name == "set_goal":
        data = svc.add_goal(s, body)
        return data, {"type": "goal_set", "data": data, "status": "saved"}
    if name == "get_profile":
        return svc.public(svc.profile(s)), None
    if name == "get_saved_plans":
        return {"workouts": svc.list_workouts(s)[:10], "diet_plans": [
            svc.public(r) for r in s.exec(svc.owned(DietPlan).where(DietPlan.status == "active")
                                         .order_by(DietPlan.id.desc()).limit(10)).all()]}, None
    if name == "get_recent_sessions":
        rows = s.exec(svc.owned(WorkoutSession).where(WorkoutSession.status.in_(("active", "completed")))
                      .order_by(WorkoutSession.id.desc()).limit(5)).all()
        return [svc.workout_out(s, svc.get(s, Workout, r.workout_id), r) | {"session_id": r.id} for r in rows], None
    if name == "get_goals":
        return [svc.goal_out(s, g) for g in s.exec(svc.owned(Goal).order_by(Goal.id).limit(30)).all()], None
    today = build_today(s)
    # Storage consent alone never permits scan summaries in a model tool result.
    today.pop("latest_scan", None)
    return today, None
