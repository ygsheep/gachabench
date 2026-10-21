# -*- coding: utf-8 -*-
"""Agent 预留：/api/agent/* 返回 501（P6 填充，v1 不接 LLM/MCP）。"""
from fastapi import APIRouter

router = APIRouter(prefix="/api/agent")


@router.post("/apply")
async def agent_apply():
    from fastapi import Response

    return Response(status_code=501, content="Not Implemented (LLM/MCP 后置)")