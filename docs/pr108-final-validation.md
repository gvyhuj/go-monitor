# PR #108 최종 검증 — 2026-10-29

검증 기준: `dcf8677` 및 이 검증에서 추가한 Pages 실패 방지 조건.
운영 DB·외부 수집 API·GitHub Pages에는 쓰기를 수행하지 않았다. 합성 DB와 사이트는 `/tmp/go-final`에서 생성했다. 화면 캡처와 실행 스크립트·로그는 클라우드 작업 경로 `/workspace/pr108-validation`에 보관했다.

## 결과와 범위

| 항목 | 결과 | 확인 범위 |
|---|---|---|
| Python 전체 | 통과 | 232개. 수집 파싱, 분류, 반응·시세, AI 기록, DB, 암호화, 백업, 자동화 관련 기존 회귀와 Pages 실패 재현 포함 |
| Node 기존 회귀 | 통과 | 15개(13 테스트 + 2 검사 파일), JS 구문 검사 |
| 로컬 실제 브라우저 | 통과 | 시스템 Chromium, PC 1280×900 / 모바일 390×900. 주가 기록·일별 표·수집 현황·기사 목록/상세·기사 대시보드 화면 로딩, KOSDAQ 토글, 당일/5/20 탭, 검색·태그 필터, AI 질문 표시, 기록 저장·새로고침·재조회 |
| 사건 연결·분리 | 통과 | 브라우저 폼으로 합성 사건 연결, 확인 상태 및 멤버 조회, 분리 후 사건 수 확인 |
| 화면 품질 | 통과 | 두 크기에서 JavaScript pageerror 없음, 문서 가로 넘침 0px. 캡처로 그래프·표·상세·기록 폼 확인 |
| 암호화 정적 화면 | 통과 | 로컬 HTTP로 빌드 결과 제공. 두 화면 크기에서 비밀번호 로그인·복호화·뉴스 그래프·기사 상세·20거래일 탭 확인 |
| 누락 구간 표시 | 통과 | 합성 시세의 누락 구간 점선 화면 및 기존 Node 보간·누락 계산 제외 검사. KOSDAQ 시각 불일치 Python 회귀 |
| 추정 기여분 | 자동 검사 통과 | 음수/초과/0 계수, 합계 정합, %와 %p 구분, AI 문구. 이번 브라우저 합성 DB에는 해당 이벤트 drawer 표본을 별도로 만들지 않았음 |
| main DB 구조 이행 | 통과 | main `3800540`의 DB 코드로 만든 합성 DB에 현재 init/migrate 두 번 적용. 기존 27개 테이블의 모든 행 동일, integrity_check=ok |
| 사이트 데이터 | 통과 | 실제 refresh_items/build로 12개 산출물 생성. 전체 복호화 후 원래 JSON 일치. 합성 기사 제목 평문 및 DB 파일이 정적 산출물에 없는지 확인 |
| DB 백업·복원 | 통과 | 합성 DB 암호화 백업·복원, 전체 테이블 행 동일, integrity_check=ok, 원본 해시 불변, 오답 비밀번호 거부 |
| public_refresh_check | 통과 (후속 보완) | 아래 ‘후속 보완’ 참조. 합성 정적 사이트·Playwright Chromium으로 자동 갱신·입력 중 보류·선택 유지 확인 |
| 실시간 운영 수집·운영 DB | 미검증 | 실제 운영 데이터/비밀키/API를 사용하지 않음. 파싱·저장·자동화 회귀 통과가 운영 API 가용성이나 실제 데이터 정확성을 보장하지 않음 |
| 실제 Pages 배포 | 미실행 | 로컬 산출물·암호화·워크플로 조건만 검증. GitHub 권한·운영 secret·실제 배포 성공 여부는 미확인 |

## 발견 및 보완: 실패한 빌드의 배포 방지

`sitebuild.build`는 HTML을 복사한 뒤 데이터를 암호화한다. 암호화 함수에 실패를 주입했을 때 `index.html`은 남고 `d/live.bin`은 생성되지 않는 상태를 재현했다. 기존 monitor 워크플로는 `always()`와 index.html 존재만으로 업로드·배포를 허용했다.

두 Pages 단계 조건을 `success() && hashFiles('_site/index.html') != ''`로 바꿨다. `cloud_job.py`는 정적 빌드 예외를 다시 던지고 종료코드 1을 반환하므로 실패한 실행의 산출물 업로드·배포가 차단된다. 이전 단계 수집 실패도 있으면 기존 정상 배포를 유지한다. DB 상태 저장·릴리스 백업의 `always()` 조건은 유지했다. 재현 및 조건 회귀는 `tests/test_pages_failure_guard.py`에 추가했다.

## 남은 확인

자동 테스트와 합성 환경의 로컬/정적 UI 검증은 통과했다(public_refresh_check는 아래 후속 보완에서 통과). 운영 원시 시세, 과거 묶음의 사실관계, 배포 시각 근거 및 운영 비밀키로의 복원은 확인하지 않았다. 따라서 이를 포함한 ‘운영 전체 검증 완료’로 해석하면 안 된다. main 병합 및 운영 배포는 수행하지 않았다.

## 후속 보완 (2026-10-29)

### 기사 분류 3종 통일

- 사건 관찰 분류를 기존 뉴스 화면과 같은 세 가지(당사 보도자료 `company_press_release` / 외부기사 `external_article` / 공시 `disclosure`)로 통일했다. 뉴스 반응 필터의 ‘외부 기업 발표’ 버튼과 상세 수정 선택지를 제거했다.
- 기존 데이터 보존: `news_events.metadata`, `news_event_history`, `news_event_records`, `articles.kind_override`에 저장된 `external_company_announcement`는 바꾸지 않는다. 읽을 때만 외부기사로 해석하고 사건 상세에 `legacy_event_kind`로 원래 값을 남긴다. 이전 화면에서 보낸 요청이 늦게 들어와도 외부기사로 저장하고 이력에 `requested_event_kind`를 기록한다. 그 밖의 값은 계속 거부한다.
- 뉴스 반응 그래프의 평균선(`rx-avg`)과 CSS는 변경하지 않았다.
- 검증: `tests/test_observatory.py`에 3종 상수, 저장값·이력·`kind_override` 보존, 보관 기록 정규화, 이전 값 요청 처리 회귀 추가. `tests/observatory_check.cjs`에 필터 버튼 4개(전체 포함)·표 표시·필터 동작 회귀 추가(이전 app.js에서 실패 확인). 이전 분류가 저장된 합성 사이트를 실제 Chromium 1280×900 / 390×844로 열어 필터·표·상세 선택지가 3종으로 보이고 ‘외부 기업 발표’ 문구 없음, pageerror 없음, 가로 넘침 0px 확인.

### public_refresh_check 현행화

- 이전 스크립트는 `demo` ID와 기본 비활성 통계 상세(`.verdict-box`, 예상 대비 차이)를 전제해 현행 사이트에서 끝나지 않았다. 이제 `tests/fixtures/public_refresh_site.py`가 합성 DB로 실제 `refresh_items`/`build`를 실행해 숫자 ID 기사 3건의 암호화 사이트를 만들고, 검사 서버는 `live.published_at`과 상세 매체명만 배포 차수별로 바꾼다.
- 시나리오: 새 배포 자동 반영(같은 기사·주소·목록 선택 유지) → 목록에서 다른 기사 선택 → 검색 입력 중 갱신 보류(입력값·포커스 유지) → 입력을 벗어난 뒤 보류된 배포 반영(두 번째 기사 선택 유지) → 새 배포가 없으면 다시 그리지 않음. 매 단계 실제 `live.bin` 재요청을 확인한 뒤 판정하며 pageerror가 없어야 한다.
- 실행: `node tests/public_refresh_check.cjs` (사이트 경로 인자 생략 시 자동 생성; 기존처럼 경로 지정 가능). 기본은 설치된 Playwright Chromium, `PW_CHANNEL=msedge`로 Edge 사용.
- 검사 자체 검증: app.js에서 입력 중 보류 조건 제거 / 선택 유지 제거 / 자동 반영 중단 / 변경 없을 때도 다시 그리기를 각각 임시로 주입했을 때 모두 실패함을 확인하고 원복했다.
- CI(`observatory-tests.yml`)에는 Playwright 설치가 필요해 이번에는 추가하지 않았다.

### 실행 결과

- `.venv/bin/python -m pytest tests -q` — **235 passed**
- `node --check web/app.js`; `node --test tests/display_consistency_check.cjs tests/observatory_check.cjs tests/reaction_alignment_check.cjs tests/dashboard_badges_check.cjs tests/primary_analysis_check.cjs` — **16 passed**; `node tests/dashboard_badges_check.cjs`, `node tests/primary_analysis_check.cjs` 통과
- `node tests/public_refresh_check.cjs` — 통과
- `git diff --check` 통과
- 운영 DB·운영 배포·main 병합은 수행하지 않았다.
