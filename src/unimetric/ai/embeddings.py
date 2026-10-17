"""임베딩 provider — 텍스트를 벡터로 변환(교체식: ollama | openai | none).

설계 의도:
- 텍스트(테이블/컬럼/지표 설명, 질문)를 의미 벡터로 바꾼다. pgvector는 저장·검색만
  하므로 '벡터를 만드는' 책임은 여기에 둔다.
- 기본은 로컬(Ollama) — 키 불필요, 데이터가 외부로 안 나감(프라이버시). 운영자가
  .env로 OpenAI 등으로 교체 가능. provider=none이면 검색은 어휘(lexical)로 폴백.
"""

import os

import httpx

from ..core.logconf import log


def provider() -> str:
    """현재 임베딩 provider 이름(ollama|openai|none)."""
    return os.getenv("EMBED_PROVIDER", "none").lower()


def is_enabled() -> bool:
    """임베딩(벡터 검색) 사용 가능 여부."""
    return provider() != "none"


def dim() -> int:
    """임베딩 차원(벡터 컬럼 크기와 일치해야 함). 모델 바꾸면 EMBED_DIM도 변경."""
    return int(os.getenv("EMBED_DIM", "1024"))  # bge-m3=1024, e5-base=768


def model_name() -> str:
    return os.getenv("EMBED_MODEL", "bge-m3")


def embed(texts: list[str]) -> list[list[float]]:
    """문자열 목록을 임베딩 벡터 목록으로 변환한다."""
    if not texts:
        return []
    p = provider()
    log.info("[embed] provider=%s model=%s n=%d (예: %r)", p, model_name(), len(texts),
             (texts[0][:50] if texts else ""))
    fn = _ollama if p == "ollama" else _openai if p == "openai" else None
    if fn is None:
        raise RuntimeError(
            "임베딩 provider가 설정되지 않았습니다. EMBED_PROVIDER=ollama|openai 로 설정하세요."
        )
    # 한 번에 다 보내면 큰 배치에서 타임아웃나므로 chunk로 나눠 호출한다.
    batch = int(os.getenv("EMBED_BATCH", "32"))
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        out.extend(fn(texts[i:i + batch]))
        log.info("[embed] %d/%d 완료", min(i + batch, len(texts)), len(texts))
    return out


def embed_one(text: str) -> list[float]:
    """문자열 하나를 임베딩한다."""
    return embed([text])[0]


def _ollama(texts: list[str]) -> list[list[float]]:
    """로컬 Ollama 임베딩(/api/embed 배치, 키 불필요)."""
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    resp = httpx.post(
        f"{base}/api/embed",
        json={"model": model_name(), "input": texts},
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["embeddings"]


def _openai(texts: list[str]) -> list[list[float]]:
    """OpenAI 임베딩(text-embedding-3-small 등)."""
    key = os.environ["OPENAI_API_KEY"]
    base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("EMBED_MODEL", "text-embedding-3-small")
    resp = httpx.post(
        f"{base}/embeddings",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "input": texts},
        timeout=120,
    )
    resp.raise_for_status()
    return [d["embedding"] for d in resp.json()["data"]]
