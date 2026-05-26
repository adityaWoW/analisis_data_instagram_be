from pydantic import BaseModel
from typing import Optional

class AnalyzeRequest(BaseModel):
    file_id: str
    sheet_name: Optional[str] = None