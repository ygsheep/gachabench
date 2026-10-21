# -*- coding: utf-8 -*-
"""系统能力 API：弹原生「选择文件夹」对话框。

为什么要走后端：浏览器里 `showDirectoryPicker()` 出于安全只交出
`FileSystemDirectoryHandle`，**拿不到绝对路径**，而工作区注册要的正是绝对路径。
后端就跑在本机、和用户同一个桌面会话，所以由它弹系统对话框最直接。

⚠️ `POST /pick-folder` 是**阻塞**的：对话框是模态的，请求会一直挂到用户选完或取消。
所以默认 300 秒兜底；前端要显示「等待选择…」并留好取消/失败提示。
"""
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

import folder_pick

router = APIRouter(prefix="/api/system", tags=["system"])


class PickFolderBody(BaseModel):
    title: str = "选择文件夹"
    initial: str = ""


@router.get("/folder-picker")
async def folder_picker_capability():
    """能力探测 —— 没有可用实现时前端直接不显示「浏览」按钮，不留死按钮。"""
    return folder_pick.available()


@router.post("/pick-folder")
async def pick_folder(body: PickFolderBody):
    """弹选择框（阻塞直到用户选完 / 取消）。取消时 `path` 为 null。"""
    try:
        return await run_in_threadpool(folder_pick.pick_folder, body.title, body.initial)
    except folder_pick.FolderPickerBusy as e:
        # 409：已经有一个对话框在等了 —— 别弹出第二个模态窗
        raise HTTPException(status_code=409, detail=str(e)) from e
    except folder_pick.FolderPickerTimeout as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except folder_pick.FolderPickerUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
