from typing import Literal

from pydantic import BaseModel


class SwipeIn(BaseModel):
    action: Literal["like", "pass", "superlike"]
