# LAVAR 기술 가이드

언어: [English](LAVAR_README.md) | [한국어](LAVAR_README_KOR.md) | [日本語](LAVAR_README_JPN.md)

LAVAR는 이 저장소의 다단계 의약품 수요 예측 연구에 사용된 모델이다. 관측 변수를 저차원 잠재 상태로 압축하고, 잠재 동학을 학습한 뒤, 의약품별 예측 헤드로 미래 수요를 산출한다. 이 문서는 서송·이푸른 연구에서 사용한 구현 계약을 설명하며 임상적 유효성을 주장하지 않는다.

## 구조

### 1단계: 공유 잠재 동학

정렬된 공변량 `X ∈ R^(T×Dx)`에 대해 다음을 수행한다.

1. 공변량 표준화
2. MLP 인코더를 통한 잠재 상태 변환
3. VAR 또는 GRU를 이용한 잠재 상태의 시간 동학 학습
4. MLP 디코더를 통한 공변량 재구성

학습 손실은 재구성 손실과 잠재 동학 손실을 결합한다. 선택적 다단계 감독은 rollout한 잠재 상태와 미래 관측의 인코딩을 비교한다.

### 2단계: 의약품 수요 헤드

2단계에서는 1단계 백본을 동결한다. 각 의약품 타깃은 학습 구간의 비영(非零) 비율에 따라 분리된다.

- dense: 비영 비율 `0.70` 이상
- ultra-sparse: 비영 비율 `0.005` 이하
- sparse: 두 기준 사이

각 그룹에 별도 헤드를 학습한다. 비교한 두 설정에서 dense와 sparse 타깃은 결정론적 GRU delta 헤드를 사용하고, ultra-sparse 타깃은 ZINB 헤드를 사용한다. Delta 예측은 가장 최근 수요에서 누적되며, 비음수 제약·학습 데이터 기반 guardrail·정수 반올림을 거친다. ZINB의 점예측 경로는 헤드의 `mu` 출력을 사용한다.

## 논문 설정

| 설정 | S04_GRU_H32_L1 | S11_LATENT16 |
|---|---:|---:|
| 잠재 차원 | 8 | 16 |
| 인코더 은닉층 | 32, 16 | 64, 32 |
| 디코더 은닉층 | 16, 32 | 32, 64 |
| 잠재 동학 | VAR | VAR |
| Dense/sparse 헤드 | GRU | GRU |
| Ultra-sparse 헤드 | ZINB | ZINB |
| GRU 폭/층수 | 32 / 1 | 32 / 1 |
| 1단계 epoch 상한 | 200 | 200 |
| 2단계 epoch 상한 | 200 | 300 |

공통 설정은 7일 잠재 이력, 14일 예측 기간, batch size 256, 최초 730일 학습 요건, 조기 종료, 1단계 공급 이력 미사용이다.

## 공개 API와 데이터 계약

```python
from lavar import EvaluationResults, LAVARConfig, LAVARForecaster, RollingEvaluator
```

```text
X: (T, Dx) 일별 공변량
y: (T, Dy) 일별 비음수 의약품 수요
forecast: (H, Dy)
```

`fit(X, y)`는 두 단계를 학습한다. `fit_stage1_shared`와 `fit_stage2_private`는 단계별 학습을 제공한다. `predict(X_recent, y_recent)`에서 최근 타깃 이력은 delta 헤드의 누적 기준값으로 사용된다.

## Rolling 평가

논문 워크플로는 expanding-history rolling origin을 사용한다. 각 origin의 스케일링과 학습은 예측 구간 이전 정보만 사용한다. 다음 14일을 예측한 뒤 origin을 14일 이동한다. 저장된 체크포인트는 1단계 signature와 fold별 2단계 상태를 분리해 재사용 관계를 추적한다.

비유한 예측은 마지막 관측값 예측으로 대체하며, 학습 데이터로 계산한 상한을 넘는 예측은 clip한다. 이러한 guardrail 사용은 fold metadata에 기록되며 평가 파이프라인의 명시적 일부다.

## 해석 제한

- 이 모델은 연구용 의약품 수요를 예측하며 환자 결과나 치료 결정을 예측하지 않는다.
- 음의 skill은 평균적으로 마지막 관측값 naive baseline보다 fold MSE가 크다는 뜻이다.
- 5개 난수 시드의 분위수는 시드 민감도를 나타낼 뿐 보정된 예측구간이 아니다.
- 잠재 차원, 입력 열, 이력 길이 또는 bucket index를 바꾸면 관련 체크포인트를 다시 학습해야 한다.
- 모델 비교는 동일 병원·rolling origin·타깃 집합·집계 방식 안에서만 해석해야 한다.

연구별 전처리와 앙상블 로직은 `paper_code/`에 있다.
