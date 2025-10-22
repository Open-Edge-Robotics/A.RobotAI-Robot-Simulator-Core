from typing import Optional
from pydantic import BaseModel

class SimulationContext(BaseModel):
    id: int
    name: str
    pattern_type: str
    namespace: str
    
class StepContext(BaseModel):
    id: Optional[int] = None
    step_order: Optional[int] = None
    template_id: Optional[int] = None
    autonomous_agent_count: Optional[int] = None
    execution_time: Optional[int] = None
    delay_after_completion: Optional[int] = None
    repeat_count: Optional[int] = None