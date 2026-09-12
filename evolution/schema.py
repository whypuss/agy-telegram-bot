"""
Evolution Schema: Data structures for uncontrolled-free, policy-gated evolution.
"""
from dataclasses import dataclass, field, asdict
from typing import Literal, Optional, List, Dict, Any
import time

CandidateType = Literal["user_profile", "system_facts", "policy_proposal", "skill_patch"]
ScopeType = Literal["permanent", "temporary", "session_only"]
ProposalStatus = Literal["pending_approval", "approved", "rejected", "expired"]
PolicyStatus = Literal["active", "stale", "archived"]

@dataclass
class EvolutionCandidate:
    candidate_type: CandidateType
    scope: ScopeType
    rule_id: str                          # kebab-case identifier
    summary: str                          # Imperative summary (<=50 words)
    root_cause: str                       # Root cause behind the failure/correction
    confidence: float                     # Confidence score 0.0 - 1.0
    evidence: Optional[str] = None        # Extracted turn or tool call snippet
    affected_behavior: Optional[str] = None  # What behavior this rule governs; evidence must exercise it
    # Optional structured form. `summary` stays the single-line rule and remains
    # the only required field; these sharpen it when the evaluator can fill them.
    trigger: Optional[str] = None         # When the rule applies
    constraint: Optional[str] = None      # What is forbidden or required
    verification: Optional[str] = None    # How to prove compliance
    pinned: bool = False                  # Security hardline, immune to archival. Never set by the pipeline.
    skill_class: Optional[str] = None     # Target class-level skill (e.g. server-operations)
    requires_approval: bool = False       # Policy and Skill always require approval

@dataclass
class Proposal:
    id: str
    proposal_type: CandidateType
    summary: str
    root_cause: str
    evidence: str
    pinned: bool
    skill_class: Optional[str]
    confidence: float
    created_at: int
    affected_behavior: str = ""
    trigger: str = ""
    constraint: str = ""
    verification: str = ""
    status: ProposalStatus = "pending_approval"
    resolved_at: Optional[int] = None
    approved_by: Optional[int] = None

@dataclass
class VersionedPolicy:
    id: str
    summary: str
    root_cause: str
    evidence: str
    pinned: bool
    version: int
    created_at: int
    last_updated: int
    last_used_at: int
    affected_behavior: str = ""   # Absent on the handwritten bootstrap policy, by design
    trigger: str = ""
    constraint: str = ""
    verification: str = ""
    status: PolicyStatus = "active"
