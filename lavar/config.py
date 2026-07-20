from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field, model_validator


class LAVARConfig(BaseModel):
    device: Literal["cpu", "mps", "cuda"] = Field(
        default="mps", description="Device to use for training"
    )
    num_workers: int = 0

    # Window
    dyn_p: int = 7  # History length used as VAR order input

    # training data input
    batch_size: int = 64
    train_days: int = 365 * 3  # ~3 years for training.
    horizon: int = 14  # 14 days

    # Supply history augmentation
    use_supply_history: bool = False
    # Stage 1 shared backbone default: do not consume supply history.
    stage1_use_supply_history: bool = False

    # stage 1 (LAVAR + VAR Dynamics)
    latent_dim: int = 8
    latent_dynamics_type: Literal["var", "gru"] = "var"
    dynamics_gru_hidden_dim: int = 32
    encoder_hidden: List[int] = Field(default_factory=lambda: [32, 16])
    decoder_hidden: List[int] = Field(default_factory=lambda: [16, 32])
    encoder_dropout: float = 0.0
    lr_lavar: float = 1e-3
    epochs_lavar: int = 100
    lambda_dyn: float = 1.0  # weight for latent dynamics loss
    lambda_recon: float = 1.0  # weight for reconstruction loss
    multi_step_latent_supervision: bool = True  # z rollout against future encoded z
    early_stop_patience_lavar: int | None = 20
    min_delta: float = 1e-6

    # stage 2 (Supply Model)
    # Trained via *density split* only:
    #   - dense targets: NB head
    #   - sparse targets: ZINB head
    #   - ultra-sparse targets: ZINB head (separate head)
    #
    # Buckets are computed on TRAIN windows only and are defined by nonzero rate:
    #   nonzero_rate(j) = mean_t[ y_tj > 0 ]
    #   - dense:        nonzero_rate >= dense_nonzero_rate_thr
    #   - ultra_sparse: nonzero_rate <= ultra_nonzero_rate_thr
    #   - sparse:       otherwise
    stage2_mode: Literal["baseline", "supply_history_latent"] = "baseline"
    stage2_use_explicit_lag_coeff: bool = False
    stage2_head_type: Literal["mlp", "gru"] = "mlp"
    stage2_delta_nonneg_mode: Literal["clamp", "softplus", "softplus_annealed"] = "clamp"
    stage2_softplus_beta_start: float = 1.0
    stage2_softplus_beta_end: float = 8.0
    supply_hidden: List[int] = Field(default_factory=list)
    gru_hidden_dim: int = 32
    gru_num_layers: int = 1
    gru_dropout: float = 0.0
    horizon_loss_weight: Literal["uniform", "linear"] = "uniform"
    lr_supply: float = 1e-3
    weight_decay_supply: float = 0.0
    epochs_supply: int = 100
    early_stop_patience_supply: int | None = 20
    dense_nonzero_rate_thr: float = 0.70
    ultra_nonzero_rate_thr: float = 0.005

    # Forecast guardrails (rolling inference)
    pred_guardrail_quantile: float = 0.995
    pred_guardrail_multiplier: float = 2.0
    pred_guardrail_min_upper: float = 1.0
    forecast_round_to_int: bool = True
    zero_gate_k: int = 7

    @model_validator(mode="after")
    def _validate_stage2_mode(self) -> "LAVARConfig":
        positive_integer_fields = (
            "batch_size",
            "dyn_p",
            "dynamics_gru_hidden_dim",
            "epochs_lavar",
            "epochs_supply",
            "gru_hidden_dim",
            "gru_num_layers",
            "horizon",
            "latent_dim",
            "train_days",
        )
        for field_name in positive_integer_fields:
            value = getattr(self, field_name)
            if value < 1:
                raise ValueError(f"{field_name} must be positive. Got {value}.")
        if any(
            width < 1 for width in self.encoder_hidden + self.decoder_hidden + self.supply_hidden
        ):
            raise ValueError("All hidden-layer widths must be positive.")
        if not 0.0 <= self.encoder_dropout < 1.0:
            raise ValueError("encoder_dropout must be in [0, 1).")
        if not 0.0 <= self.gru_dropout < 1.0:
            raise ValueError("gru_dropout must be in [0, 1).")
        if not 0.0 <= self.ultra_nonzero_rate_thr <= self.dense_nonzero_rate_thr <= 1.0:
            raise ValueError("Density thresholds must satisfy 0 <= ultra <= dense <= 1.")
        if self.stage2_mode == "supply_history_latent" and not self.use_supply_history:
            raise ValueError(
                "stage2_mode='supply_history_latent' requires use_supply_history=True."
            )
        if self.stage2_mode == "supply_history_latent" and not self.stage1_use_supply_history:
            raise ValueError(
                "supply_history_latent mode requires stage1_use_supply_history=True "
                "because LAVAR encoder input dim is fixed at Stage 1 training time."
            )
        if self.stage2_softplus_beta_start <= 0 or self.stage2_softplus_beta_end <= 0:
            raise ValueError("stage2 softplus beta values must be positive.")
        return self

    @classmethod
    def builder(cls) -> LAVARConfigBuilder:
        return LAVARConfigBuilder()


class LAVARConfigBuilder:
    def __init__(self) -> None:
        self._overrides: dict[str, object] = {}

    def device(self, device: str) -> LAVARConfigBuilder:
        self._overrides["device"] = device
        return self

    def latent(
        self,
        dim: int | None = None,
        encoder: List[int] | None = None,
        decoder: List[int] | None = None,
    ) -> LAVARConfigBuilder:
        if dim is not None:
            self._overrides["latent_dim"] = dim
        if encoder is not None:
            self._overrides["encoder_hidden"] = encoder
        if decoder is not None:
            self._overrides["decoder_hidden"] = decoder
        return self

    def horizon(self, h: int | None = None, history: int | None = None) -> LAVARConfigBuilder:
        if h is not None:
            self._overrides["horizon"] = h
        if history is not None:
            self._overrides["dyn_p"] = history
        return self

    def supply_history(self, flag: bool) -> LAVARConfigBuilder:
        self._overrides["use_supply_history"] = flag
        return self

    def training(
        self,
        epochs: int | None = None,
        lr: float | None = None,
        batch_size: int | None = None,
    ) -> LAVARConfigBuilder:
        if epochs is not None:
            self._overrides["epochs_lavar"] = epochs
        if lr is not None:
            self._overrides["lr_lavar"] = lr
        if batch_size is not None:
            self._overrides["batch_size"] = batch_size
        return self

    def supply_training(
        self, epochs: int | None = None, lr: float | None = None
    ) -> LAVARConfigBuilder:
        if epochs is not None:
            self._overrides["epochs_supply"] = epochs
        if lr is not None:
            self._overrides["lr_supply"] = lr
        return self

    def density(
        self, dense_thr: float | None = None, ultra_thr: float | None = None
    ) -> LAVARConfigBuilder:
        if dense_thr is not None:
            self._overrides["dense_nonzero_rate_thr"] = dense_thr
        if ultra_thr is not None:
            self._overrides["ultra_nonzero_rate_thr"] = ultra_thr
        return self

    def build(self) -> LAVARConfig:
        return LAVARConfig(**self._overrides)
