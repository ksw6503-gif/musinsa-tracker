# 🖤 MUSINSA PULSE — 개인용 무신사 가격 추적 & 재입고 알림 웹앱 (폴센트 스타일)

서버 유지비 **0원**, PC를 켜두지 않아도 **GitHub Actions**와 **GitHub Pages**를 통해 24시간 클라우드에서 동작하는 개인용 무신사 가격 하락·목표가 도달·타겟 사이즈 재입고 텔레그램 알림 시스템입니다.

## 📁 프로젝트 폴더 구조

```text
musinsa-price-tracker/
├── .github/
│   └── workflows/
│       └── tracker.yml      # 30분 주기 24시간 무료 자동 실행 GitHub Actions 설정
├── index.html               # 모바일 최적화 웹 대시보드 (단일 파일, GitHub Pages 호스팅용)
├── tracker.py               # 무신사 가격/사이즈 재고 파싱 및 텔레그램 알림 백엔드 봇
├── items.json               # 추적할 무신사 상품 리스트 및 현재 상태 저장소
├── state.json               # 직전 가격/최저가/품절 상태 비교용 히스토리 파일 (자동 생성)
├── requirements.txt         # Python 의존성 패키지 (requests, beautifulsoup4)
└── README.md                # 10분 완성 단계별 설치 및 스마트폰 사용 가이드
```

## 🚀 10분 완성 단계별 세팅 가이드

1. **텔레그램 봇 생성:** 텔레그램에서 `@BotFather` 검색 ➔ `/newbot` 입력 ➔ 발급된 **HTTP API Token** 복사 ➔ 생성된 봇에게 아무 메시지나 1회 전송 후 `https://api.telegram.org/bot<토큰>/getUpdates` 접속하여 내 **Chat ID** 확인.
2. **GitHub 저장소 업로드 & Secrets 등록:** GitHub 새 저장소 생성 후 전체 파일 업로드 ➔ `Settings` > `Secrets and variables` > `Actions`에서 `TELEGRAM_BOT_TOKEN`과 `TELEGRAM_CHAT_ID` 등록.
3. **스마트폰 웹 대시보드(GitHub Pages) 연동:** `Settings` > `Pages`에서 `Branch: main` 저장 후 생성된 URL을 스마트폰 브라우저에서 열고 **홈 화면에 추가**하여 앱처럼 사용.
