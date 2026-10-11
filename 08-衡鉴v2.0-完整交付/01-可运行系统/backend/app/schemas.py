# -*- coding: utf-8 -*-
"""接口契约层（Pydantic 模型）

这一层是"前后端契约的代码化表达"：
《API接口规范文档》里写的字段，在这里必须一一对应；
FastAPI 会自动生成 /docs 的 OpenAPI 文档，前后端联调以它为准，不靠口头约定。
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    contract_id: int
    code: str
    reused: bool = False
    sha256: str = ""
    auto_run: bool = True
    message: str = ""


class PullRequest(BaseModel):
    count: int = Field(3, ge=1, le=20, description="从审批系统待办中拉取的合同数量")
    auto_run: bool = Field(True, description="拉取后是否自动启动审查流水线")


class TaskBrief(BaseModel):
    id: int
    code: str
    name: str
    applicant: str = ""
    dept: str = ""
    biz_type: str = ""
    counterparty: str = ""
    amount: float | None = None
    currency: str = "CNY"
    status: str
    risk_level: str
    conclusion: str = ""
    block_reason: str = ""
    file_type: str = ""
    high_cnt: int = 0
    medium_cnt: int = 0
    low_cnt: int = 0
    created_at: str = ""
    updated_at: str = ""


class ClauseOut(BaseModel):
    id: int
    clause_type: str
    clause_label: str
    heading: str
    page: int
    para_index: int
    char_start: int
    char_end: int
    text: str


class ParagraphOut(BaseModel):
    page: int
    para_index: int
    char_start: int
    char_end: int
    text: str
    clause_type: str
    clause_label: str
    is_heading: bool = False


class DocumentOut(BaseModel):
    file_name: str
    file_type: str
    parse_engine: str
    parse_status: str
    page_count: int
    char_count: int
    sha256: str
    ocr_confidence: float | None = None
    block_reason: str = ""


class MetaItemOut(BaseModel):
    key: str
    label: str
    value: str
    page: int = 1
    para_index: int = 0
    char_start: int = -1
    char_end: int = -1


class FindingOut(BaseModel):
    id: int
    rule_id: str
    title: str
    level: str
    level_label: str
    clause_type: str
    clause_label: str
    judge_mode: str
    evidence_text: str
    page: int
    para_index: int
    char_start: int
    char_end: int
    reason: str
    legal_basis: str
    advice: str
    advice_source: str
    adopted: bool = False
    adopted_text: str = ""


class ReviewOut(BaseModel):
    risk_summary: str
    conclusion: str
    focus_points: list[str] = []
    high_cnt: int = 0
    medium_cnt: int = 0
    low_cnt: int = 0
    reviewer_note: str = ""
    generated_by: str = "template"
    rule_version: str = ""


class ContractDetail(BaseModel):
    task: TaskBrief
    document: DocumentOut | None = None
    meta: list[MetaItemOut] = []
    profile: dict = {}
    review: ReviewOut | None = None
    writeback: dict = {}
    clauses: list[ClauseOut] = []


class AdoptRequest(BaseModel):
    adopted: bool = True
    adopted_text: str = Field("", description="法务编辑后的最终条款文本，留空则采用系统建议")


class AnnotationRequest(BaseModel):
    note: str = Field("", description="法务补充意见")
    reviewer: str = Field("", description="法务人员姓名")


class WritebackRequest(BaseModel):
    dry_run: bool = Field(True, description="true=预览模式，只暂存不外发；false=真实写入审批系统")
    operator: str = ""
    reviewer_note: str = ""


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="自然语言检索词，如「帮我看下付款条件」")


class RuleUpdateRequest(BaseModel):
    title: str | None = None
    level: str | None = None
    clause_type: str | None = None
    reason: str | None = None
    legal_basis: str | None = None
    params: dict | None = None


class EvalRunRequest(BaseModel):
    cases: int = Field(0, ge=0, description="0 表示跑全部黄金样例")
    persist: bool = Field(True, description="是否写入评测历史表")


# ------------------------------------------------------------------ 人工审核（HITL）
class FindingEdit(BaseModel):
    """法务对单条风险项的修改：可改等级、改建议文本。"""
    finding_id: int
    level: str | None = Field(None, description="high / medium / low，留空表示不修改")
    advice: str | None = Field(None, description="法务改写后的推荐条款")
    adopted: bool | None = Field(None, description="是否采纳")


class HumanReviewRequest(BaseModel):
    """人工审核决策。

    approve —— 确认系统审查结果，允许进入回写
    edit    —— 修改风险等级与建议后确认（修改内容写入 payload_json 与 findings 表）
    reject  —— 驳回审查结果，任务回到 pending 并重新审查
    resume  —— 从阻塞中断点恢复（等价于重试，但保留中断上下文）
    """
    action: str = Field(..., description="approve / edit / reject / resume")
    actor: str = Field("", description="操作人")
    note: str = Field("", description="审核意见")
    edits: list[FindingEdit] = Field(default_factory=list, description="action=edit 时的修改清单")


class HitlState(BaseModel):
    interrupt_at: str = ""
    interrupt_reason: str = ""
    status: str = "pending_review"
    available_actions: list[str] = []
    last_action: dict = {}
    history: list[dict] = []


class ApiError(BaseModel):
    error: str
    detail: str = ""
