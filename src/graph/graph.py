"""AgentCore Platform v1.0"""

# Cat 1 — Single technical capability (use-case-agnostic): resource metric
# collection + threshold-based structured health report. The proposal's
# 3-step workflow (validate/resolve -> collect/evaluate -> sanitize/report)
# maps 1:1 onto AgentBaseGraph's 3 fixed domain slots — see
# docs/02_design.md "Architecture Overview" for the node mapping.

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.schemas.trust_level import TrustLevel

from src.nodes.pre_process_node import PreProcessNode
from src.nodes.main_node import MainNode
from src.nodes.post_process_node import PostProcessNode
from src.schemas.state import State


class AgentHealthResourceMonitorAgent(AgentBaseGraph):
    """Resource/health monitoring agent: multi-backend metric collection
    (psutil / cAdvisor / Prometheus) + threshold evaluation + structured
    report generation."""

    required_trust_level = TrustLevel.VERIFIED_EXTERNAL

    @property
    def name(self) -> str:
        return "AgentHealthResourceMonitorAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects InitializeNode + FinalizeNode

        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = MainNode(metric_source=self.config.get("metric_source"))
        self._nodes["post_process"] = PostProcessNode()
