"""Pydantic models for the email analyzer API."""

from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class AnalyzerRule(BaseModel):
    id: str
    name: str
    severity: Optional[str] = "medium"
    source: Optional[str] = None


class AnalyzerRuleResult(BaseModel):
    rule: AnalyzerRule
    result: Optional[bool] = None
    success: bool
    error: Optional[str] = None
    execution_time: float


class AnalyzerQueryResult(BaseModel):
    name: Optional[str] = None
    result: Optional[Any] = None
    success: bool = True
    error: Optional[str] = None


class ScanResult(BaseModel):
    filename: str
    msg_size_bytes: int
    eml_size_bytes: int
    sender: Optional[str]
    recipients: List[str]
    subject: Optional[str]
    date: Optional[str]
    attachment_names: List[str]
    rule_results: List[AnalyzerRuleResult]
    query_results: List[AnalyzerQueryResult]
    rules_matched_count: int
    scan_duration_ms: float
    analyzer_url: str = "https://analyzer.sublime.security"
    recommended_action: str = "No threat indicators detected."
    recommendation_level: str = "clean"
