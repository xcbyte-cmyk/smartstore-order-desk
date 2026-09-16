# 스마트스토어 주문 워크스페이스

기존 상품관리 MCP에 추가한 로컬 웹 UI입니다. 주문 데이터는 상품관리 DB와 분리된 `orders_workspace.sqlite3`에 저장됩니다. 기존 MCP 도구와 상품관리 실행 스위치는 그대로 유지됩니다.

## 실행

Python 3.11 이상 환경에서 `pip install -e '.[ui,dev]'` 후 프로젝트 폴더에서 아래 명령을 실행합니다.

```powershell
.venv\Scripts\python.exe -m korea_ecommerce_mcp.orders_web
```

Windows용 실행 진입점은 파일 잠금으로 중복 실행을 막습니다. 한 프로세스·한 워커로 실행합니다. 주소는 `http://127.0.0.1:8435`이며 이 PC에서만 접속됩니다. 저장소 루트의 `start-orderdesk.cmd`로도 실행할 수 있습니다.

## 세 가지 기능

1. **자동 수집:** 기본 1시간, 30분·2시간·3시간·6시간으로 변경 가능. 마지막 완료된 구간을 기록하고 2분 중첩 조회해 상품주문번호로 갱신합니다. 페이지가 여러 개이면 끝까지 조회하며 구간은 23시간 이하로 분할합니다. 첫 수집은 최근 24시간, 별도 기간 재수집은 최근 1·3·7·30일의 변경 내역입니다. 출고 대기 주문과 송신 결과 확인 대기 주문은 상세 재조회합니다.
2. **발주서 다운로드:** 선택한 주문을 `.xlsx`로 내보냅니다. 주문번호·연락처·우편번호는 문자열로 유지하고 수식으로 실행될 수 있는 상품명도 문자열로 기록합니다. 다운로드 자체로 발주 확인이나 주문 상태 변경을 하지 않습니다.
3. **송장 업로드·송신:** 양식은 `상품주문번호 / 택배사 / 송장번호`. 중복 ID, 수식, 숫자형 ID·송장번호, 미수집 주문 등을 행별 검사합니다. 미리보기는 15분 동안 유효하며 한 번만 실행됩니다. 정상 행만 확인 후 최대 30개씩 송신합니다. 일반 택배·발주 확인 완료·결제 완료·클레임 없는 주문에 한해 동작합니다.

## 연결과 송신

처음 연결할 때는 [Client ID·Client Secret 준비 및 저장 방법](../README.md#smartstore-connection)을 따라 진행하세요. 애플리케이션 ID·시크릿 확인, API호출 IP 등록, 화면 입력과 저장, 저장된 연결 확인, 첫 주문 수집까지 순서대로 안내합니다.

UI의 연결 설정에서 Client ID, Client Secret을 입력합니다. 본인 스토어 SELF 인증의 판매자 계정 ID는 비웁니다. 이 값은 프로젝트 `.env`에 저장되며 읽기 API는 키를 반환하지 않습니다. API 허용 IP와 주문 권한은 네이버 커머스API센터 설정에 따라 별도 확인해야 합니다.

송장은 UI의 `실제 송장 송신 허용`을 켠 뒤 업로드 검토와 송신 확인을 거쳐 실행합니다. 이 UI의 송장 권한은 별도 DB 설정이며 **기존 상품관리 MCP의 `KEIC_ALLOW_MUTATIONS=false`를 변경하지 않습니다.**

API 성공 응답은 `accepted`, 주문 상세에서 동일 택배사·송장과 배송 진행 상태가 확인되면 `success`입니다. 통신 오류, 결과 누락, 송신 중 프로세스 종료는 `unknown`으로 기록하며 자동 재송신을 막습니다. `주문 수집 / 상태 재조회`로 반영 여부를 확인합니다. 결과 미확인이 계속되면 판매자센터에서 확인해야 하며 앱에서 강제로 재송신하지 않습니다.

## 운영 범위

- 브라우저를 닫아도 서버 프로세스가 실행 중이면 수집합니다. PC 종료·절전 상태에서는 동작하지 않습니다. 재부팅 후 실행 파일을 다시 열어야 합니다. Windows 로그인 자동 시작은 등록하지 않았습니다.
- 로컬 DB에는 배송을 위한 개인정보가 포함됩니다. 외부 공개 호스팅 용도가 아닙니다.
- 발주서는 범용 양식이며 특정 택배사 전용 업로드 서식은 아닙니다.
- 클레임, 방문수령, 직접전달, 해외 택배 등은 이 송신 UI의 처리 범위에서 제외됩니다.
- 최초 설치 확인은 모의 API로 수행했으며 실계정 인증·실주문 송신은 API 키 입력 후 검증이 필요합니다.

## API 근거

- [변경 상품 주문 내역](https://apicenter.commerce.naver.com/docs/commerce-api/current/seller-get-last-changed-status-pay-order-seller)
- [상품 주문 상세](https://apicenter.commerce.naver.com/docs/commerce-api/current/seller-get-product-orders-pay-order-seller)
- [발송 처리](https://apicenter.commerce.naver.com/docs/commerce-api/current/seller-dispatch-product-orders-pay-order-seller)

## 검증

`tests/test_orders_workspace.py`: 주문 중복·수집 실패 커서, 페이지 이동, 엑셀 번호 보존, 업로드 유효성, 부분 실패, 송신 후 재조회, 중복 송신 차단, CSRF, 주기 설정 유지, 스케줄 자동 실행을 모의 API와 임시 DB로 점검합니다.
