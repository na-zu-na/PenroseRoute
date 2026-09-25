"""Composition factory; callers provide real transaction, solver and validator adapters."""
from collections.abc import Callable
from app.core.config import Settings
from app.integrations.agent.client import BedrockExplanationClient
from app.integrations.agent.contracts import SolverResult, ValidationReport
from .orchestration import RecoveryOrchestrator
from .workflow import RecoveryApplication, RecoveryWorkflow


def create_recovery_workflow(
    application: RecoveryApplication,
    solver: Callable[[str], SolverResult],
    validator: Callable[[str, SolverResult], ValidationReport],
    settings: Settings,
    evidence_projector=None,
) -> RecoveryWorkflow:
    client = None
    if settings.agent_explanation_provider == "bedrock":
        if not settings.bedrock_model_id:
            raise ValueError("BEDROCK_MODEL_ID is required for the bedrock provider")
        client = BedrockExplanationClient(settings.bedrock_model_id, region_name=settings.aws_region,
            endpoint_url=settings.bedrock_endpoint_url, connect_timeout=settings.agent_connect_timeout_seconds,
            read_timeout=settings.agent_read_timeout_seconds)
    return RecoveryWorkflow(application, RecoveryOrchestrator(solver, validator, client, evidence_projector))


def create_default_recovery_workflow(session_factory, settings: Settings):
    from app.integrations.optimization.solver import solve, validate
    from .application import SqlRecoveryApplication
    from .evidence import project_evidence
    return create_recovery_workflow(SqlRecoveryApplication(session_factory), solve, validate, settings, project_evidence)
