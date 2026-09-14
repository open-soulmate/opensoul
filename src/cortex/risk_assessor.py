"""Risk Assessor — 理解式风险评估；支持多文件风险聚合。"""

import logging
from src.models.cognitive import Intent, Risk, RiskAssessment, TaskContext
from src.cortex.project_memory import ProjectMemory

logger = logging.getLogger(__name__)


class RiskAssessor:
    """理解式风险评估器"""

    def __init__(self, project_memory: ProjectMemory, experience_memory=None):
        self.project = project_memory
        self.experience = experience_memory

    async def assess(self, intent: Intent, task_ctx: TaskContext) -> RiskAssessment:
        all_risks: list[Risk] = []

        for file_path in intent.target_files:
            all_risks.extend(self._assess_single_file(file_path, intent))

        if len(intent.target_files) > 3:
            all_risks.append(Risk("多文件改动", "medium", f"同时修改{len(intent.target_files)}个文件"))

        if self.experience:
            similar_failures = await self.experience.get_similar_failures(intent)
            if similar_failures:
                level = "high" if len(similar_failures) >= 3 else "medium"
                all_risks.append(Risk("历史失败经验", level, f"类似操作曾失败{len(similar_failures)}次"))

        overall = self._aggregate(all_risks)
        recommendation = self._recommend(all_risks)
        return RiskAssessment(risks=all_risks, overall_level=overall, recommendation=recommendation)

    def _assess_single_file(self, path: str, intent: Intent) -> list[Risk]:
        risks = []
        if self.project.is_core_file(path):
            risks.append(Risk("核心文件修改", "high", f"{path}是项目核心文件"))
        impact = self.project.get_impact(path)
        if impact.risk_level in ("high", "critical"):
            risks.append(Risk(f"影响{len(impact.direct_impact)}个文件", impact.risk_level, f"直接依赖：{', '.join(impact.direct_impact[:5])}"))
        if intent.change_size == "large":
            risks.append(Risk("大规模改动", "medium", "建议分步执行"))
        return risks

    def _aggregate(self, risks: list[Risk]) -> str:
        if not risks:
            return "low"
        levels = [r.level for r in risks]
        if "critical" in levels:
            return "critical"
        if levels.count("high") >= 2:
            return "critical"
        if "high" in levels:
            return "high"
        if levels.count("medium") >= 2:
            return "high"
        if "medium" in levels:
            return "medium"
        return "low"

    def _recommend(self, risks: list[Risk]) -> str:
        if any(r.level == "critical" for r in risks):
            return "风险过高，建议人工审查后执行"
        if any(r.level == "high" for r in risks):
            return "建议：先备份，增量编辑，改完验证"
        if any(r.level == "medium" for r in risks):
            return "建议：增量编辑，改完检查"
        return "正常执行"
