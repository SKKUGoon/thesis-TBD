# LAVAR 技術ガイド

言語: [English](LAVAR_README.md) | [한국어](LAVAR_README_KOR.md) | [日本語](LAVAR_README_JPN.md)

LAVAR は、本リポジトリの多段階医薬品需要予測研究で用いたモデルである。観測変数を潜在状態へ圧縮し、潜在ダイナミクスを学習した後、医薬品別の予測ヘッドから将来需要を出力する。本書は Seosong・Epurun 研究で使用した実装契約を説明するものであり、臨床的有効性を主張するものではない。

## 構成

### Stage 1: 共有潜在ダイナミクス

整列済み共変量 `X ∈ R^(T×Dx)` に対して、次を行う。

1. 共変量の標準化
2. MLP encoder による潜在状態への変換
3. VAR または GRU による潜在状態の時間発展の学習
4. MLP decoder による共変量の再構成

学習損失は再構成損失と潜在ダイナミクス損失を組み合わせる。任意の多段階教師信号では、rollout した潜在状態と将来観測の encoding を比較する。

### Stage 2: 医薬品需要ヘッド

Stage 2 では Stage 1 backbone を固定する。各ターゲットは、学習区間内の非ゼロ率で分類する。

- dense: 非ゼロ率 `0.70` 以上
- ultra-sparse: 非ゼロ率 `0.005` 以下
- sparse: その中間

各群に別々のヘッドを学習する。実装は deterministic delta head と低レベル NB/ZINB head をサポートする。本論文の二設定では GRU delta head を用いる。Delta 予測は直近需要を起点として積分され、非負制約、学習データに基づく guardrail、整数丸めを適用する。

## 論文で用いた設定

| 設定 | S04_GRU_H32_L1 | S11_LATENT16 |
|---|---:|---:|
| 潜在次元 | 8 | 16 |
| Encoder 隠れ層 | 32, 16 | 64, 32 |
| Decoder 隠れ層 | 16, 32 | 32, 64 |
| 潜在ダイナミクス | VAR | VAR |
| Stage 2 head | GRU | GRU |
| GRU 幅/層数 | 32 / 1 | 32 / 1 |
| Stage 1 epoch 上限 | 200 | 200 |
| Stage 2 epoch 上限 | 200 | 300 |

共通設定は、7日間の潜在履歴、14日予測、batch size 256、初期730日学習、early stopping、Stage 1 での供給履歴連結なしである。

## Public API とデータ契約

```python
from lavar import EvaluationResults, LAVARConfig, LAVARForecaster, RollingEvaluator
```

```text
X: (T, Dx) 日次共変量
y: (T, Dy) 日次の非負医薬品需要
forecast: (H, Dy)
```

`fit(X, y)` は両 Stage を学習する。`fit_stage1_shared` と `fit_stage2_private` は段階別学習を提供する。`predict(X_recent, y_recent)` では、直近ターゲット履歴が delta head の積分基準となる。

## Rolling 評価

論文 workflow は expanding-history rolling origin を使用する。各 origin の scaling と fitting は予測窓より前の情報だけを使い、次の14日を予測して origin を14日進める。Checkpoint は Stage 1 signature と fold 別 Stage 2 state を分離し、backbone の再利用関係を監査可能にする。

非有限予測は last-value forecast へ置き換え、学習データ由来の上限を超える値は clip する。Guardrail の使用は fold metadata に記録され、評価 pipeline の明示的な一部である。

## 解釈上の制約

- 本モデルは研究用医薬品需要を予測し、患者転帰や治療判断を予測しない。
- 負の skill は、平均 fold MSE が last-value naive baseline より大きいことを意味する。
- 5 seed 間の分位点は seed 感度であり、較正済み予測区間ではない。
- 潜在次元、入力列、履歴長、bucket index の変更には関連 checkpoint の再学習が必要である。
- 比較は同一病院、rolling origin、target 集合、metric 集計方法の範囲内で解釈する。

研究固有の前処理と ensemble logic は `paper_code/` に置かれている。
