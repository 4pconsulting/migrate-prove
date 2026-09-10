from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RiskLevel(str, Enum):
    critical = "critical"
    master = "master"
    high_volume = "high_volume"


class Concern(str, Enum):
    migration = "migration"
    transformation = "transformation"
    reconciliation = "reconciliation"


class MappingKind(str, Enum):
    direct = "direct"
    transform = "transform"
    coerce = "coerce"
    default = "default"
    surrogate = "surrogate"


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"
    ERROR = "ERROR"


class NormalizeSpec(BaseModel):
    trim: bool = True
    upper: bool = False
    date_format: str = "%Y-%m-%d"
    datetime_format: str = "%Y-%m-%dT%H:%M:%S"
    decimal_places: int | None = None
    null_token: str = ""
    empty_as_null: bool = True


class WhenThen(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    when: str | None = None
    then: Any = None
    else_value: Any = Field(default=None, alias="else")


class Mapping(BaseModel):
    source: str | None = None
    target: str
    kind: MappingKind = MappingKind.direct
    lookup: dict[str, Any] | None = None
    lookup_default: Any | None = None
    lookup_on_missing: Literal["fail", "passthrough", "null"] = "fail"
    rules: list[WhenThen] = []
    expression: str | None = None
    default: Any | None = None
    normalize: NormalizeSpec = Field(default_factory=NormalizeSpec)
    in_hash: bool = True
    type: str | None = None
    nullable: bool = True
    max_length: int | None = None


class Stratum(BaseModel):
    name: str
    where: str
    rate: float = 1.0

    @field_validator("rate")
    @classmethod
    def rate_bounds(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("stratum rate must be between 0 and 1")
        return value


class SamplePolicy(BaseModel):
    strategy: Literal["full", "stratified"] = "full"
    strata: list[Stratum] = []


class Metric(BaseModel):
    name: str
    source_sql: str
    target_sql: str
    group_by: list[str] = []
    tolerance_abs: float = 0.0
    tolerance_pct: float | None = None


class Invariant(BaseModel):
    name: str
    kind: Literal["sql", "orphans", "temporal"] = "sql"
    sql: str | None = None
    expect: int | float = 0
    columns: list[str] = []
    child_table: str | None = None
    child_key: str | None = None
    parent_table: str | None = None
    parent_key: str | None = None
    concern: Concern = Concern.reconciliation


class Entity(BaseModel):
    name: str
    source_table: str
    target_table: str
    source_schema: str | None = None
    target_schema: str | None = None
    business_key: list[str]
    source_key: list[str] | None = None
    risk: RiskLevel = RiskLevel.master
    mappings: list[Mapping]
    volume_slices: list[list[str]] = []
    metrics: list[Metric] = []
    invariants: list[Invariant] = []
    sample: SamplePolicy = Field(default_factory=SamplePolicy)
    filter_source: str | None = None
    filter_target: str | None = None
    failure_probes: list[str] = []

    def mapping_for_target(self, target: str) -> Mapping | None:
        for mapping in self.mappings:
            if mapping.target == target:
                return mapping
        return None

    def source_key_columns(self) -> list[str]:
        if self.source_key:
            return self.source_key
        keys: list[str] = []
        for target_col in self.business_key:
            mapping = self.mapping_for_target(target_col)
            if mapping is None or not mapping.source:
                raise ValueError(
                    f"Entity {self.name}: business key {target_col!r} needs "
                    "source_key or a mapping with a source column"
                )
            keys.append(mapping.source)
        return keys


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    name: str
    entities: list[Entity]


class CheckResult(BaseModel):
    id: str
    level: str
    title: str
    entity: str
    concern: Concern
    status: CheckStatus
    expected: Any = None
    actual: Any = None
    variance: Any = None
    message: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = 0.0


class SuiteConfig(BaseModel):
    name: str
    contract: str
    source: dict[str, Any]
    target: dict[str, Any]
    output_dir: str = "artifacts"
    continue_on_fail: bool = True
    levels: list[str] = Field(
        default_factory=lambda: ["1", "2", "3", "4", "5", "hash", "probes"]
    )
