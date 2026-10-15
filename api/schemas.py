"""API 요청 바디(Pydantic 모델) 모음 — 라우터들이 공유한다."""

from pydantic import BaseModel


class ProjectCreate(BaseModel):
    """단체(프로젝트) 생성 요청."""

    key: str
    name: str | None = None
    metrics_yaml: str = ""


class MetricsYaml(BaseModel):
    """지표 카탈로그 YAML 저장 요청."""

    metrics_yaml: str


class SourceConfig(BaseModel):
    """외부 DB 접속 설정(연결 테스트/가져오기/라이브 공용)."""

    kind: str = "postgres"  # postgres | mysql | sqlite
    host: str | None = None
    port: int | None = None
    user: str | None = None
    password: str | None = None
    database: str | None = None


class SourceImport(BaseModel):
    """외부 DB 전체 복사(잡) 요청. tables 비우면 전체 테이블."""

    key: str
    name: str | None = None
    config: SourceConfig
    tables: list[str] | None = None


class LiveProjectCreate(BaseModel):
    """라이브(복사 안 함) 단체 생성 요청."""

    key: str
    name: str | None = None
    config: SourceConfig


class MetricQuery(BaseModel):
    """지표 조회 요청."""

    project: str
    metric: str
    group_by: list[str] = []
    filters: list[str] = []


class BuilderReq(BaseModel):
    """지표 빌더 미리보기/저장 요청(spec = builder.add_metric 명세).

    type=sql(자유 SQL 지표)면 table은 필요 없다.
    """

    project: str
    table: str = ""
    spec: dict
    group_by: list[str] = []


class AiMetricReq(BaseModel):
    """단발성 자연어 → 지표 명세 생성 요청."""

    project: str
    table: str
    question: str


class ChatReq(BaseModel):
    """대화형 지표 생성 요청. 서버가 세션으로 기록하므로 새 메시지 1개만 보낸다."""

    project: str
    message: str
    session_id: str | None = None


class Ask(BaseModel):
    """자연어 질의 요청."""

    project: str
    question: str
