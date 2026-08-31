from typing import Literal

from pydantic import BaseModel


class ServiceStatus(BaseModel):
    service: Literal["repotriage-api"] = "repotriage-api"
    status: Literal["healthy", "ready", "unavailable"]
