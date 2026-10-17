"""공용 로거 — RAG/채팅 파이프라인의 단계별 로그를 stdout에 찍는다.

테스트할 때 서버 콘솔에서 "임베딩 호출 → 검색 top-k → action → 실행 SQL/행수"
흐름을 눈으로 볼 수 있게 한다. `from ..core.logconf import log` 후 log.info(...).
"""

import logging
import sys

log = logging.getLogger("unimetric")

if not log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s [unimetric] %(message)s", "%H:%M:%S"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)
    log.propagate = False
