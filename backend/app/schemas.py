from datetime import date as Date
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, field_validator, model_validator

NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Name = Annotated[str, Field(min_length=1, max_length=200)]
Meal = Literal["breakfast", "lunch", "dinner", "snack"]
Day = Literal["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]

class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)

class ProfileIn(Input):
    name: Name | None = None
    height_cm: Annotated[float, Field(gt=0, le=300)] | None = None
    weight_kg: Annotated[float, Field(gt=0, le=600)] | None = None
    age: Annotated[int, Field(ge=10, le=110)] | None = None
    sex: Literal["male", "female"] | None = None
    kcal_target: Annotated[int, Field(gt=0, le=15000)] | None = None
    protein_g: Annotated[int, Field(gt=0, le=1000)] | None = None
    carbs_g: Annotated[int, Field(gt=0, le=2000)] | None = None
    fat_g: Annotated[int, Field(gt=0, le=1000)] | None = None
    preferences: list[Name] | None = Field(default=None, max_length=30)
    allergies: list[Name] | None = Field(default=None, max_length=30)
    equipment: list[Name] | None = Field(default=None, max_length=30)
    experience: Literal["unspecified", "beginner", "intermediate", "advanced"] | None = None
    available_days: list[Day] | None = Field(default=None, max_length=7)
    goals_context: str | None = Field(default=None, max_length=2000)
    timezone: str | None = None
    scan_storage_consent: bool | None = None
    scan_ai_sharing_consent: bool | None = None
    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, v):
        if v is not None:
            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("Unknown IANA timezone") from None
        return v

class ChatIn(Input):
    message: str = Field(min_length=1, max_length=8000)
    # Optional meal photo (JPEG, base64). Sent to Claude for this turn only; never stored.
    image_jpeg_base64: str | None = Field(default=None, max_length=7_000_000)
    request_id: UUID | None = None
    conversation_id: UUID | None = None
    plan_mode: Literal["save", "propose"] = "save"

class SetIn(Input):
    kg: NonNegative
    reps: int = Field(ge=0, le=1000)
    is_warmup: bool = False

class SetPatch(Input):
    kg: NonNegative | None = None
    reps: int | None = Field(default=None, ge=0, le=1000)
    done: bool | None = None

class Nutrition(Input):
    kcal: NonNegative
    protein_g: NonNegative = 0
    carbs_g: NonNegative = 0
    fat_g: NonNegative = 0

class FoodIn(Nutrition):
    date: Date | None = None
    captured_at: AwareDatetime | None = None
    meal: Meal
    name: Name
    quantity: str = Field(default="", max_length=200)
    source: Literal["manual", "coach", "synthetic"] = "manual"
    nutrition_provenance: Literal["user_estimate", "coach_estimate", "nutrition_label", "synthetic"] = "user_estimate"
    is_estimate: bool = True

class GoalIn(Input):
    title: Name
    kind: Literal["strength", "body", "nutrition", "habit"]
    unit: str = Field(default="", max_length=40)
    start_value: float = 0
    current_value: float | None = None
    target_value: float
    deadline: Date | None = None
    status: Literal["active", "completed"] = "active"
    progress_source: Literal["explicit", "weekly_sessions", "daily_protein"] = "explicit"
    @model_validator(mode="after")
    def compatible_progress(self):
        if self.progress_source == "weekly_sessions" and (self.kind != "habit" or self.unit != "sessions"):
            raise ValueError("Weekly sessions requires a habit goal in sessions")
        if self.progress_source == "daily_protein" and (self.kind != "nutrition" or self.unit != "g"):
            raise ValueError("Daily protein requires a nutrition goal in g")
        return self

class GoalPatch(Input):
    current_value: float | None = None
    status: Literal["active", "completed"] | None = None

class ExerciseIn(Input):
    name: Name
    sets: int = Field(ge=1, le=20)
    reps: int = Field(ge=1, le=100)
    kg: NonNegative | None = None

class WorkoutIn(Input):
    day_label: Day
    name: Name
    notes: str = Field(default="", max_length=2000)
    exercises: list[ExerciseIn] = Field(min_length=1, max_length=20)

class WorkoutPlanIn(Input):
    plan_name: Name
    weeks: int = Field(ge=1, le=52)
    workouts: list[WorkoutIn] = Field(min_length=1, max_length=7)
    replaces_plan_id: int | None = Field(default=None, gt=0)

class DietMeal(Nutrition):
    meal: Meal
    name: Name

class DietPlanIn(Input):
    name: Name
    kcal: int = Field(gt=0, le=15000)
    meals: list[DietMeal] = Field(min_length=1, max_length=20)
    replaces_plan_id: int | None = Field(default=None, gt=0)

class StartWorkoutIn(Input):
    workout_id: int = Field(gt=0)

class LogSetIn(SetIn):
    exercise_name: Name
    session_id: int | None = Field(default=None, gt=0)
    exercise_id: int | None = Field(default=None, gt=0)
    set_index: int | None = Field(default=None, ge=0, le=1000)

class EmptyIn(Input):
    pass

class Metric(Input):
    identifier: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    value: float = Field(allow_inf_nan=False)
    unit: Literal["cm", "kg", "percent", "ratio", "index"]  # ratio/index: unitless Visualize health indicators
    @model_validator(mode="after")
    def metric_range(self):
        if self.value <= 0 or (self.unit == "percent" and self.value > 100):
            raise ValueError("Measurement is outside its unit range")
        return self

class ScanIn(Input):
    client_scan_id: UUID
    captured_at: AwareDatetime
    metrics: list[Metric] = Field(min_length=1, max_length=40)
    source: Literal["visualize_sdk", "manual", "synthetic"]
    source_metadata: dict[str, Annotated[str, Field(max_length=200)]] = Field(default_factory=dict)
    @model_validator(mode="after")
    def summary_only(self):
        allowed = {"sdk_version", "device_model", "mapping_version"}
        if not set(self.source_metadata) <= allowed:
            raise ValueError("Only SDK version, device model, and mapping version metadata are accepted")
        ids = [m.identifier for m in self.metrics]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate metric identifiers")
        return self


# Response models document the existing phone decoder fields. Additional metadata is optional.
class UserOut(BaseModel):
    id: int
    name: str
    height_cm: float | None = None
    weight_kg: float | None = None
    age: int | None = None
    sex: str | None = None
    kcal_target: int | None = None
    protein_g: int | None = None
    carbs_g: int | None = None
    fat_g: int | None = None
    bmi: float | None = None
    bmi_category: str | None = None
    targets_source: str | None = None
    preferences: list[str] = []
    allergies: list[str] = []
    equipment: list[str] = []
    experience: str = "unspecified"
    available_days: list[str] = []
    goals_context: str = ""
    timezone: str
    scan_storage_consent: bool = False
    scan_ai_sharing_consent: bool = False

class SetOut(BaseModel):
    id: int
    exercise_id: int
    session_id: int
    set_index: int
    kg: float
    reps: int
    is_warmup: bool
    done: bool

class ExerciseOut(BaseModel):
    id: int
    workout_id: int
    name: str
    position: int
    target_sets: int
    target_reps: int
    target_kg: float | None
    sets: list[SetOut]

class WorkoutOut(BaseModel):
    id: int
    name: str
    day_label: str
    notes: str
    status: Literal["planned", "active", "done"]
    started_at: str | None
    finished_at: str | None
    plan_id: int
    session_id: int | None
    exercises: list[ExerciseOut]

class FoodOut(BaseModel):
    id: int
    date: str
    captured_at: str
    meal: Meal
    name: str
    quantity: str
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float
    source: str
    nutrition_provenance: str
    is_estimate: bool

class FoodTotals(BaseModel):
    kcal: float
    protein_g: float
    carbs_g: float
    fat_g: float

class FoodDayOut(BaseModel):
    date: str
    meals: dict[Meal, list[FoodOut]]
    totals: FoodTotals

class GoalOut(BaseModel):
    id: int
    title: str
    kind: str
    unit: str
    start_value: float
    current_value: float
    target_value: float
    deadline: str | None
    status: str
    progress_source: str

class DietOut(BaseModel):
    id: int
    name: str
    kcal: int
    meals: list[DietMeal]
    created_at: str
    status: str

class Card(BaseModel):
    type: Literal["workout_plan", "food_logged", "diet_plan", "goal_set", "set_logged"]
    data: dict
    id: str | None = None
    status: str | None = None
    action: dict | None = None

class ChatOut(BaseModel):
    reply: str
    cards: list[Card]

class ChatMessageOut(BaseModel):
    id: int
    role: str
    content: str
    cards: list[Card]
    created_at: str
    conversation_id: str | None = None
    request_ref: str | None = None

class KcalOut(BaseModel):
    target: float
    eaten: float
    left: float

class MacroOut(BaseModel):
    eaten: float
    target: float

class MacrosOut(BaseModel):
    protein: MacroOut
    carbs: MacroOut
    fat: MacroOut

class BMIOut(BaseModel):
    value: float | None
    category: str | None

class WeekOut(BaseModel):
    sessions_done: int
    sessions_target: int
    days: list[bool]

class NextWorkoutOut(BaseModel):
    id: int
    name: str
    exercise_count: int
    est_minutes: int

class ScanOut(BaseModel):
    id: int
    client_scan_id: str
    captured_at: str
    metrics: list[Metric]
    source: str
    source_metadata: dict[str, str]
    verification: str

class TodayOut(BaseModel):
    date: str
    kcal: KcalOut
    macros: MacrosOut
    bmi: BMIOut
    weight_kg: float | None
    week: WeekOut
    next_workout: NextWorkoutOut | None
    coach_tip: str
    targets_source: str | None
    latest_scan: ScanOut | None

class OK(BaseModel):
    ok: bool

class SessionOut(BaseModel):
    id: int
    workout_id: int
    status: str
    source: str
    started_at: str | None
    finished_at: str | None
