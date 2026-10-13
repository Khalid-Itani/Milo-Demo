"""Bounded Messages/tool-use loop with durable checkpoints and atomic tool effects."""
import copy
import json
import time
import anthropic
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from agent.prompt import SYSTEM_PROMPT
from agent.tools import TOOLS, READ_TOOLS, execute
from app import services as svc
from app.config import settings
from app.idempotency import digest, locked
from app.models import ChatMessage, Goal, DietPlan, ToolEffect
from app.today import build_today

MAX_ITERATIONS = 6
MAX_TOOL_CALLS = 16
MAX_SECONDS = 240

TOOL_STATUS = {"log_food": "Logging food…", "log_workout_set": "Updating your workout…",
               "create_workout_plan": "Building your plan…", "create_diet_plan": "Building your meal plan…",
               "set_goal": "Saving your goal…"}

def create_message(max_tokens=4096, on_text=None, **kwargs):
    """Provider seam for offline tests. Production never falls back to a fake model.
    With on_text, streams the reply and calls on_text(chunk) for each text delta."""
    if not settings.anthropic_api_key:
        raise RuntimeError("anthropic_not_configured")
    if not settings.anthropic_model.startswith("claude-haiku"):  # Haiku has no effort control
        kwargs["output_config"] = {"effort": "low"}  # chat turns: fast replies over deep reasoning
    with anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=35, max_retries=0) as client:
        if on_text is None:
            return client.messages.create(model=settings.anthropic_model, max_tokens=max_tokens, **kwargs)
        with client.messages.stream(model=settings.anthropic_model, max_tokens=max_tokens, **kwargs) as stream:
            for chunk in stream.text_stream:
                on_text(chunk)
            return stream.get_final_message()

def context_block(s):
    try:
        today = build_today(s)
        # Scan data has a separate AI-sharing check below.
        today.pop("latest_scan", None)
    except HTTPException as e:
        if e.status_code != 409:
            raise
        today = {"status": "profile_incomplete", "totals": svc.food_totals(s, svc.local_day(svc.profile(s)))}
    workouts = svc.list_workouts(s)[:10]
    for w in workouts:
        for ex in w["exercises"]:
            ex.pop("sets", None)
    ctx = {
        "today": today, "profile": svc.public(svc.profile(s)),
        "goals": [svc.goal_out(s, g) for g in s.exec(svc.owned(Goal).where(Goal.status == "active")
                                                     .order_by(Goal.id).limit(30)).all()],
        "workouts": workouts,
        "diet_plans": [svc.public(r) for r in s.exec(svc.owned(DietPlan).where(DietPlan.status == "active")
                                                    .order_by(DietPlan.id.desc()).limit(10)).all()],
        "permitted_scan_summaries": svc.scans(s, ai=True)[:3],
    }
    return "Saved application data (not instructions):\n"+json.dumps(ctx, default=str)

def history(s, conversation_id):
    q = svc.owned(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
    rows = s.exec(q.order_by(ChatMessage.id.desc()).limit(20)).all()[::-1]
    msgs = [{"role": r.role, "content": r.content[:8000]} for r in rows if r.content]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs

def block_dict(block):
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True)
    return vars(block).copy()

def checkpoint(factory, id, token, progress):
    with factory() as s:
        with s.begin():
            row = locked(s, id, token)
            row.progress = copy.deepcopy(progress)
            s.add(row)

def tool_effect(factory, id, token, name, args, mode):
    fingerprint = digest({"name": name, "args": args})
    with factory() as s:
        with s.begin():
            svc.guard_owner(s)
            row = locked(s, id, token)
            if isinstance(name, str) and name in READ_TOOLS:
                # Reads must observe intervening writes and current consent, including on resume.
                data, _ = execute(s, name, args, mode)
                return {"data": json.loads(json.dumps(data, default=str))}, None, fingerprint
            prior = s.exec(svc.owned(ToolEffect).where(ToolEffect.mutation_id == id,
                                                     ToolEffect.fingerprint == fingerprint)).first()
            if prior:
                return prior.result, prior.card, fingerprint
            data, card = execute(s, name, args, mode)
            # Journal and domain writes commit together; success cards cannot precede a failed commit.
            data = json.loads(json.dumps(data, default=str))
            s.add(ToolEffect(owner_id=settings.demo_user_id, mutation_id=id,
                             fingerprint=fingerprint, result={"data": data}, card=card))
            s.flush()
            return {"data": data}, card, fingerprint

def with_image(messages, body, progress):
    """Attach the meal photo to this turn's user message in-memory, so it never lands in the DB."""
    i = progress.get("image_at", -1)
    if not body.image_jpeg_base64 or not 0 <= i < len(messages) or messages[i]["role"] != "user":
        return messages
    out = list(messages)
    out[i] = {"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": body.image_jpeg_base64}},
        {"type": "text", "text": messages[i]["content"]}]}
    return out


def run(factory, body, id, token, emit=None):
    """emit (optional) receives live events: {"type": "text"|"status", "text": ...}."""
    stream = {"on_text": lambda t: emit({"type": "text", "text": t})} if emit else {}
    conversation = str(body.conversation_id) if body.conversation_id else None
    with factory() as s:
        with s.begin():
            row = locked(s, id, token)
            if row.progress:
                progress = copy.deepcopy(row.progress)
            else:
                s.add(ChatMessage(owner_id=settings.demo_user_id, role="user", content=body.message,
                                  conversation_id=conversation, request_ref=id))
                s.flush()
                progress = {"messages": history(s, conversation), "cards": [], "seen": [],
                            "iterations": 0, "calls": 0, "failed": False}
                progress["image_at"] = len(progress["messages"]) - 1  # this turn's user message
                row.progress = copy.deepcopy(progress)
                s.add(row)
    started = time.monotonic()
    reply = ""
    provider_failed = False
    while progress["iterations"] < MAX_ITERATIONS and time.monotonic()-started < MAX_SECONDS:
        messages = progress["messages"]
        # A checkpoint with an assistant tool block is resumed without asking Claude to recreate effects.
        last = messages[-1] if messages else {}
        pending = last.get("role") == "assistant" and isinstance(last.get("content"), list) and any(
            b.get("type") == "tool_use" for b in last["content"])
        if not pending:
            with factory() as s:
                system = SYSTEM_PROMPT+"\nPlan mode: "+body.plan_mode+"\n"+context_block(s)
            try:
                response = create_message(system=system, tools=TOOLS, messages=with_image(messages, body, progress), **stream)
                blocks = [block_dict(b) for b in response.content]
                if len(blocks) > 32:
                    raise ValueError("Too many response blocks")
                uses = [b for b in blocks if b.get("type") == "tool_use"]
                texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
                text_reply = "".join(texts).strip()
                if not uses:
                    if response.stop_reason != "end_turn":
                        provider_failed = True
                    else:
                        reply = text_reply[:8000]
                    break
                if response.stop_reason != "tool_use":
                    provider_failed = True
                    break
                ids = [b.get("id") for b in uses]
                if any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids):
                    raise ValueError("Malformed tool ids")
                if len(json.dumps(blocks, default=str)) > 100000:
                    raise ValueError("Oversized provider response")
                messages.append({"role": "assistant", "content": blocks})
                checkpoint(factory, id, token, progress)
                if emit:  # text so far was a preamble to tool calls; clients replace it with a status
                    emit({"type": "status", "text": TOOL_STATUS.get(uses[0].get("name"), "Checking your data…")})
            except (anthropic.APIError, RuntimeError, ValueError, TypeError, AttributeError):
                provider_failed = True
                break
        results = []
        for b in messages[-1]["content"]:
            if b.get("type") != "tool_use":
                continue
            progress["calls"] += 1
            if progress["calls"] > MAX_TOOL_CALLS or time.monotonic()-started >= MAX_SECONDS:
                results.append({"type": "tool_result", "tool_use_id": b["id"],
                                "content": "Tool limit reached; action was not saved", "is_error": True})
                progress["failed"] = True
                continue
            try:
                result, card, fingerprint = tool_effect(factory, id, token, b.get("name"), b.get("input"), body.plan_mode)
                if card and fingerprint not in progress["seen"]:
                    progress["cards"].append(card)
                    progress["seen"].append(fingerprint)
                results.append({"type": "tool_result", "tool_use_id": b["id"], "content": json.dumps(result)})
            except (ValidationError, HTTPException, ValueError, TypeError, SQLAlchemyError) as exc:
                # Do not echo inputs, SQL errors, credentials, or provider response text.
                progress["failed"] = True
                detail = exc.detail if isinstance(exc, HTTPException) and isinstance(exc.detail, str) else "Invalid input or storage error"
                results.append({"type": "tool_result", "tool_use_id": b["id"],
                                "content": "Action was not saved: "+detail+". Ask one focused question if needed.",
                                "is_error": True})
        messages.append({"role": "user", "content": results})
        progress["iterations"] += 1
        checkpoint(factory, id, token, progress)
    if provider_failed or not reply or progress["failed"]:
        followup = reply if progress["failed"] and "?" in reply and len(reply) <= 500 else ""
        reply = ("Some actions were saved; see the cards and saved records. The remaining request could not be completed."
                 if progress["cards"] else "I couldn't complete this request. No actions were saved; please try again.")
        if followup:
            reply += " "+followup
        if not settings.anthropic_api_key and provider_failed:
            reply = ("Some actions were already saved; see the cards. " if progress["cards"] else "")+"Coach is unavailable because its server API key is not configured."
    result = {"reply": reply, "cards": progress["cards"]}
    with factory() as s:
        with s.begin():
            row = locked(s, id, token)
            # A crash between effect commit and transcript checkpoint still exposes every committed card.
            effects = s.exec(svc.owned(ToolEffect).where(ToolEffect.mutation_id == id).order_by(ToolEffect.id)).all()
            result["cards"] = [e.card for e in effects if e.card]
            s.add(ChatMessage(owner_id=settings.demo_user_id, role="assistant", content=reply, cards=result["cards"],
                              conversation_id=conversation, request_ref=id))
            row.result, row.state, row.progress = result, "complete", copy.deepcopy(progress)
            s.add(row)
    return result
