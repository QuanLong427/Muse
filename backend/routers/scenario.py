from fastapi import APIRouter
from pydantic import BaseModel
from services.scenario_manager import add_scenario, read_scenarios, remove_scenario
from services.memory_manager import remove_profile_scenario
from services.memory_store import DEFAULT_USER_ID, forget_memory_scenario

router = APIRouter(tags=["scenario"])


class AddScenarioRequest(BaseModel):
    name: str


@router.get("/api/scenarios")
async def get_scenarios():
    """获取场景列表"""
    scenarios = read_scenarios()
    return {"scenarios": scenarios}


@router.post("/api/scenarios")
async def create_scenario(req: AddScenarioRequest):
    """添加新场景"""
    if not req.name.strip():
        return {"error": "场景名称不能为空"}
    scenarios = add_scenario(req.name.strip())
    return {"scenarios": scenarios}


@router.delete("/api/scenarios/{name}")
async def delete_scenario(name: str):
    """删除场景"""
    scenarios = remove_scenario(name)
    forget_memory_scenario(user_id=DEFAULT_USER_ID, scenario=name)
    remove_profile_scenario(name, DEFAULT_USER_ID)
    return {"scenarios": scenarios}
