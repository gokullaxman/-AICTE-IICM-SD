from typing import Optional, List, Literal
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
    complainant_phone: Optional[str] = Field(default=None, max_length=64)
    complainant_email: Optional[str] = Field(default=None, max_length=128)

    def __init__(self, **data):
        super().__init__(**data)
        if self.pid is not None and self.pid.strip() != "":
            if not re.match(r"^1-\d{5,14}$", self.pid.strip()):
                raise ValueError("AICTE Permanent ID must follow format 1-XXXXXXX")
            self.pid = self.pid.strip()
        else:
            self.pid = None

class ExternalComplaintWebhookRequest(BaseModel):
    external_reference_id: str = Field(..., min_length=3, max_length=64)
    source_portal: str = Field(default="CPGRAMS", max_length=64) # CPGRAMS, AICTE_CITIZEN, STATE_DTE
    institution_name: str = Field(..., min_length=3, max_length=255)
    aicte_pid: Optional[str] = Field(default=None, max_length=32)
    category: Optional[str] = Field(default=None, max_length=128)
    complainant_name: str = Field(..., min_length=2, max_length=255)
    complainant_email: Optional[str] = Field(default=None, max_length=128)
    complainant_phone: Optional[str] = Field(default=None, max_length=64)
    description: str = Field(..., min_length=10, max_length=3000)
    receipt_date: Optional[str] = Field(default=None, max_length=32)
    language: Optional[str] = Field(default="en", max_length=16)

class WorkflowUpdateRequest(BaseModel):
    new_status: str = _regex_field(..., r"^(Pending|Under Process|Transferred|Escalated|Disposed)$")
    remarks: str = Field(..., min_length=5, max_length=1500)
    next_action: str = Field(..., min_length=3, max_length=500)
    supporting_doc: str = Field(default="Official_Action_Note.pdf", max_length=255)
    force_lead_override: bool = Field(default=False)

class CaseTransferRequest(BaseModel):
    to_bureau: str = _regex_field(..., r"^(RB|PGRC|AB|VC|RO)$")
    target_officer: str = Field(..., min_length=3, max_length=128)
    transfer_reason: str = Field(..., min_length=5, max_length=1000)
    next_action: str = Field(..., min_length=3, max_length=500)
    transfer_order_doc: str = Field(default="Inter_Bureau_Transfer_Memo.pdf", max_length=255)
    force_lead_override: bool = Field(default=False)

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
    enforce_action_hold: bool = Field(default=True)

class LeadBureauAssignmentRequest(BaseModel):
    lead_bureau: str = _regex_field(..., r"^(RB|PGRC|AB|VC|RO|JOINT_COMMITTEE)$")
    action_hold: bool = Field(default=True)
    coordination_directive: Optional[str] = Field(default="Designated Lead Bureau. Non-lead bureaus on HOLD.", max_length=1000)

class PIIUnmaskRequest(BaseModel):
    justification_reason: str = Field(..., min_length=5, max_length=1000) # Statutory reason under DPDP Act 2023
    statutory_reference: Optional[str] = Field(default=None, max_length=128)

class NotificationDispatchTriggerRequest(BaseModel):
    channel: str = _regex_field("ALL", r"^(ALL|EMAIL|SMS)$")
    custom_message: Optional[str] = Field(default=None, max_length=1000)

class ChatMessageItem(BaseModel):
    role: str = _regex_field(..., r"^(user|assistant)$")
    content: str = Field(..., min_length=1, max_length=2000)

class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: Optional[List[ChatMessageItem]] = Field(default_factory=list)
    conversation_id: Optional[str] = Field(default=None, max_length=64)

class ChatMessageResponse(BaseModel):
    reply: str
    conversation_id: Optional[str] = None
    category: Optional[str] = None
    sources: Optional[List[str]] = None
