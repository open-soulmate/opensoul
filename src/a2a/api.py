"""A2A Protocol API endpoints for OpenSoul.

Implements:
- GET /.well-known/agent.json - Agent Card discovery
- POST /a2a - JSON-RPC 2.0 endpoint
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.a2a.models import (
    ErrorCode,
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
)
from src.a2a.task_manager import task_manager

router = APIRouter()


@router.get("/.well-known/agent.json")
async def agent_card():
    """A2A Agent Card discovery endpoint."""
    card = task_manager.get_agent_card()
    return card.model_dump()


@router.post("/a2a")
async def a2a_endpoint(request: Request):
    """A2A JSON-RPC 2.0 endpoint."""
    try:
        body = await request.json()
        rpc_request = JSONRPCRequest(**body)
    except Exception as e:
        return JSONResponse(
            status_code=200,
            content=JSONRPCResponse(
                id=0, error={"code": ErrorCode.PARSE_ERROR, "message": f"Parse error: {e}"}
            ).model_dump(exclude_none=True),
        )

    try:
        result = await handle_rpc(rpc_request)
        return JSONResponse(
            status_code=200,
            content=JSONRPCResponse(id=rpc_request.id, result=result).model_dump(exclude_none=True),
        )
    except Exception as e:
        return JSONResponse(
            status_code=200,
            content=JSONRPCResponse(
                id=rpc_request.id, error={"code": ErrorCode.INTERNAL_ERROR, "message": str(e)}
            ).model_dump(exclude_none=True),
        )


async def handle_rpc(request: JSONRPCRequest):
    """Route JSON-RPC method to handler."""
    method = request.method
    params = request.params

    handlers = {
        "tasks/send": handle_task_send,
        "tasks/get": handle_task_get,
        "tasks/cancel": handle_task_cancel,
    }

    handler = handlers.get(method)
    if not handler:
        raise ValueError(f"Method not found: {method}")

    return await handler(params)


async def handle_task_send(params: dict) -> dict:
    """Handle tasks/send - create or continue a task."""
    task_id = params.get("id")
    message_data = params.get("message", {})
    message = Message(**message_data)

    if task_id:
        # Continue existing task
        task = await task_manager.process_task(task_id, message)
    else:
        # Create new task
        task = await task_manager.create_task(message)
        task = await task_manager.process_task(task.id, message)

    return task.model_dump(exclude_none=True)


async def handle_task_get(params: dict) -> dict:
    """Handle tasks/get - retrieve task status."""
    task_id = params.get("id")
    if not task_id:
        raise ValueError("Missing task id")

    task = await task_manager.get_task(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    return task.model_dump(exclude_none=True)


async def handle_task_cancel(params: dict) -> dict:
    """Handle tasks/cancel - cancel a task."""
    task_id = params.get("id")
    if not task_id:
        raise ValueError("Missing task id")

    task = await task_manager.cancel_task(task_id)
    if not task:
        raise ValueError(f"Task cannot be canceled: {task_id}")

    return task.model_dump(exclude_none=True)
