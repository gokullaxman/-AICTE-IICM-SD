from typing import Optional
import re
import pydantic
from pydantic import BaseModel, Field

IS_V2 = int(pydantic.__version__.split(".")[0]) >= 2

def _regex_field(default_val, regex_str: str, **kwargs):
    if IS_V2:
        return Field(default_val, pattern=regex_str, **kwargs)
    return Field(default_val, regex=regex_str, **kwargs)

class LoginRequest(BaseModel):
    email: str = Field(..., min_length=5, max_length=128)
    password: str = Field(..., min_length=4, max_length=128)

class CaseCreateRequest(BaseModel):
    pid: Optional[str] = Field(default=None, max_length=32)
    institution_name: str = Field(..., min_length=3, max_length=255)
    bureau: str = _regex_field(..., r"^(RB|PGRC|AB|VC|RO)$")
    category: str = Field(..., min_length=3, max_length=128)
    assigned_officer: str = Field(..., min_length=3, max_length=128)
    sla_days: Optional[int] = Field(default=None, ge=1, le=180)
    description: str = Field(..., min_length=10, max_length=2000)
    next_action: str = Field(..., min_length=5, max_length=500)
    complainant: str = Field(default="Stakeholder Portal Submission", min_length=3, max_length=255)

    def __init__(self, **data):
        super().__init__(**data)
        if self.pid is not None and self.pid.strip() != "":
            if not re.match(r"^1-\d{5,14}$", self.pid.strip()):
                raise ValueError("AICTE Permanent ID must follow format 1-XXXXXXX")
            self.pid = self.pid.strip()
        else:
            self.pid = None

class WorkflowUpdateRequest(BaseModel):
    new_status: str = _regex_field(..., r"^(Pending|Under Process|Transferred|Escalated|Disposed)$")
    remarks: str = Field(..., min_length=5, max_length=1500)
    next_action: str = Field(..., min_length=3, max_length=500)
    supporting_doc: str = Field(default="Official_Action_Note.pdf", max_length=255)

class CaseTransferRequest(BaseModel):
    to_bureau: str = _regex_field(..., r"^(RB|PGRC|AB|VC|RO)$")
    target_officer: str = Field(..., min_length=3, max_length=128)
    transfer_reason: str = Field(..., min_length=5, max_length=1000)
    next_action: str = Field(..., min_length=3, max_length=500)
    transfer_order_doc: str = Field(default="Inter_Bureau_Transfer_Memo.pdf", max_length=255)

class CaseEscalateRequest(BaseModel):
    escalation_authority: str = Field(..., min_length=3, max_length=128)
    escalation_reason: str = Field(..., min_length=5, max_length=1000)
    next_action: str = Field(..., min_length=3, max_length=500)
    supporting_doc: str = Field(default="Escalation_Dossier.pdf", max_length=255)

class OverlapCheckRequest(BaseModel):
    institution_name: str = Field(..., min_length=2, max_length=255)
    aicte_pid: Optional[str] = Field(default=None, max_length=32)
    receiving_bureau: str = _regex_field(..., r"^(RB|PGRC|AB|VC|RO)$")
    category: str = Field(default="Fee Refund & Overcharging", max_length=128)
    complaint_summary: str = Field(..., min_length=3, max_length=2000)

class ManualCaseLinkRequest(BaseModel):
    source_case_id: str = Field(..., min_length=4, max_length=64)
    target_case_id: str = Field(..., min_length=4, max_length=64)
    reason: str = Field(..., min_length=5, max_length=1000)
    lead_bureau: Optional[str] = _regex_field("RB", r"^(RB|PGRC|AB|VC|RO|JOINT_COMMITTEE)$")

class ManualCaseUnlinkRequest(BaseModel):
    source_case_id: str = Field(..., min_length=4, max_length=64)
    target_case_id: str = Field(..., min_length=4, max_length=64)
    reason: str = Field(..., min_length=5, max_length=1000)

class JointCoordinationRequest(BaseModel):
    lead_bureau: str = _regex_field(..., r"^(RB|PGRC|AB|VC|RO|JOINT_COMMITTEE)$")
    resolution_protocol: str = Field(..., min_length=5, max_length=255)
    directive_notes: str = Field(..., min_length=10, max_length=2000)
