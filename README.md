# SmartStore Order Desk

스마트스토어 주문 수집부터 발주서 다운로드, 송장 엑셀 송신까지 처리하는 로컬 웹 UI입니다.

[Korea E-commerce Integrated Channel MCP](https://github.com/minwoo19930301/korea-ecommerce-integrated-channel-mcp)를 기반으로 주문 관리 기능을 추가했습니다. 원본 MIT 라이선스와 상품관리 MCP 기능을 유지합니다.

## 주요 기능

| 기능 | 내용 |
| --- | --- |
| 시간마다 자동 주문 수집 | 기본 1시간 주기, 수동 수집, 기간 재수집, 상품주문번호 기준 중복 방지 |
| 발주서 다운로드 | 선택 주문의 상품·옵션·수량·수취인·연락처·주소·배송메모를 XLSX로 다운로드 |
| 송장 엑셀 업로드·송신 | 행별 오류 검토, 확인 후 정상 행 송신, 네이버 응답과 실제 반영 상태 구분 |
| 처리 내역 | 수집 결과·다운로드·송신 이력과 확인 필요 주문 표시 |

**주문 UI는 스마트스토어 전용입니다.** 원본 MCP의 쿠팡·11번가·ESM 상품관리 지원과 구분됩니다. 실제 판매자 API 키는 포함되지 않으며, 실계정 수집·발송 성공은 사용자 계정으로 확인해야 합니다.

## Windows 설치

Python 3.11 이상과 Git이 필요합니다.

```powershell
git clone https://github.com/xcbyte-cmyk/smartstore-order-desk.git
cd smartstore-order-desk
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[ui]"
Copy-Item .env.example .env
.venv\Scripts\python.exe -m korea_ecommerce_mcp.orders_web
```

브라우저에서 **http://127.0.0.1:8435** 를 엽니다. 설치 후에는 `start-orderdesk.cmd`를 실행하면 서버를 백그라운드에서 시작하고 화면을 엽니다. 이미 실행 중인 경우 기존 화면을 엽니다. 현재 기본 실행기는 Windows용입니다.

## 사용 순서

1. **스마트스토어 연결**에서 네이버 커머스 API의 Client ID와 Client Secret을 저장합니다. 본인 스토어 SELF 인증이면 판매자 계정 ID는 비워 둡니다.
2. **지금 주문 수집**으로 첫 주문을 가져옵니다. 첫 수집 범위는 최근 24시간의 변경 내역이며, 이전 기간은 **기간 재수집**을 사용합니다.
3. 주문을 선택하고 **선택 발주서 다운로드**를 누릅니다. 다운로드 자체로 발주 확인이나 주문 상태를 변경하지 않습니다.
4. **송장 업로드·송신**에서 엑셀 양식을 받아 `상품주문번호 / 택배사 / 송장번호`를 입력합니다. 번호는 텍스트 형식으로 유지합니다.
5. 업로드 검토 후 정상 행을 확인하고 송신합니다. 연결 설정에서 **실제 송장 송신 허용**을 켜야 하며, 발주 확인이 끝난 결제완료 일반 택배 주문만 처리합니다.

지원 택배사: CJ대한통운, 한진택배, 로젠택배, 우체국택배, 롯데택배, 경동택배.

## 자동 수집과 결과 확인

- 수집 주기: 30분·1시간·2시간·3시간·6시간. 기본은 1시간이며 계정 연결 전에는 대기합니다.
- 브라우저를 닫아도 서버가 실행 중이면 수집합니다. PC 종료·절전 중에는 중단됩니다. 재부팅 후 실행 파일을 다시 열어야 합니다.
- 송신 결과는 **반영 확인 / 성공 응답 후 확인 대기 / 실패 / 결과 미확인**으로 표시합니다. 불확실한 송신을 자동 재시도하지 않습니다.
- API 키, 주문 DB, 로그는 로컬에 저장되며 Git 추적 대상에서 제외됩니다. 공개 웹 서버로 노출하는 용도가 아닙니다.
- 발주서는 범용 주문 목록입니다. 특정 택배사 전용 업로드 서식, 자동 발주 확인, 클레임 처리는 포함되지 않습니다.

자세한 운영 방법은 [주문 UI 안내](docs/ORDERS_UI.md), 원본 상품관리 MCP 설정은 [MCP 설치 가이드](docs/SETUP.md)를 참고하세요.

## 개발 및 검증

```powershell
.venv\Scripts\python.exe -m pip install -e ".[ui,dev]"
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check src tests
```

주문 기능 테스트는 임시 DB와 모의 네이버 응답을 사용합니다. 주문 중복, 페이지 이동, 수집 실패 후 이어받기, 엑셀 번호 보존, 송신 일부 실패, 중복 송신 방지, 자동 수집 실행을 확인합니다. 실계정 테스트와 구분됩니다.

## 라이선스와 출처

[MIT License](LICENSE).

원본 프로젝트: [minwoo19930301/korea-ecommerce-integrated-channel-mcp](https://github.com/minwoo19930301/korea-ecommerce-integrated-channel-mcp). 원본 커밋 이력 및 라이선스를 보존하고 주문 관리 UI·백엔드를 추가했습니다.
