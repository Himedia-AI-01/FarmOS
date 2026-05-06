# jobs/

APScheduler 기반 정기 작업. `app/main.py` lifespan에서 스케줄러가 시작됩니다.

## 파일

| 파일 | 역할 |
|------|------|
| `scheduler.py` | 활성 정기 작업 등록 (`update_segments`, `sync_revenue`) |
| `generate_report.py` | 리포트 수동 생성용 job 구현. Business 섹션 비활성화로 스케줄러에는 등록하지 않음 |
| `check_shipments.py` | 배송 상태 수동 점검용 job 구현. 자동 배송 체크 배치는 등록하지 않음 |
| `update_segments.py` | RFM 분석으로 고객 세그먼트 갱신 |
