"""
Controlled Evolution System for AGY Telegram Bot.
Inspired by Hermes Agent's background learning, policy compilation, and deterministic lifecycle,
fortified with deterministic gates and human-in-the-loop approvals.
"""
from evolution.schema import EvolutionCandidate, Proposal, VersionedPolicy
from evolution.queue import enqueue_turn, init_queue
from evolution.gate import (
    approve_proposal, reject_proposal, list_pending_proposals, get_proposal,
    set_policy_pinned,
)
from evolution.worker import start_evolution_worker, set_bot_instance

__all__ = [
    "EvolutionCandidate",
    "Proposal",
    "VersionedPolicy",
    "enqueue_turn",
    "init_queue",
    "approve_proposal",
    "reject_proposal",
    "list_pending_proposals",
    "get_proposal",
    "set_policy_pinned",
    "start_evolution_worker",
    "set_bot_instance"
]

