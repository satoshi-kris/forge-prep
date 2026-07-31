"""
forge-prep: Data readiness toolkit for Mistral Forge
Audit, clean, and prepare enterprise data for custom model training.
"""

from forge_prep._version import get_version
from forge_prep.auditor import CorpusAuditor
from forge_prep.cleaner import CorpusCleaner
from forge_prep.report import ReadinessReport
from forge_prep.scorer import ReadinessScorer

__author__ = "Kris"
__version__ = get_version()

__all__ = ["CorpusAuditor", "CorpusCleaner", "ReadinessScorer", "ReadinessReport", "get_version"]
