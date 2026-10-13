from __future__ import annotations

from dataclasses import dataclass
import time

import numpy as np
import torch
from timesfm3.torch.timesfm3_forecaster import TimesFM3Forecaster


@dataclass
class MaskedTimesFM:
    forecaster: TimesFM3Forecaster
    device: str

    def predict_masked(self, target: np.ndarray, patients: np.ndarray, known: np.ndarray, patient_days: int, horizon: int=14) -> np.ndarray:
        """Decode native per-value masks, bypassing high-level automatic interpolation.

        target B,K,C; patients B,69,C; known B,Q,C+H. Return B,K,H,9.
        Missing storage remains NaN; native mask placeholders do not assert zero demand.
        """
        if target.ndim!=3 or patients.shape[0]!=target.shape[0] or patients.shape[-1]!=target.shape[-1]: raise ValueError('Unaligned target/patient tensor')
        if known.shape[-1]!=target.shape[-1]+horizon: raise ValueError('Unaligned known calendar')
        target_mask=~np.isfinite(target);patient_mask=~np.isfinite(patients)
        if patient_days<patients.shape[-1]: patient_mask[:,:,:-patient_days]=True
        # All masked positions carry numerical placeholders; masks retain missing semantics.
        def tensor(a: np.ndarray) -> torch.Tensor:
            return torch.as_tensor(a,device=self.device)
        with torch.inference_mode():
            result=self.forecaster.model.decode(tensor(np.where(target_mask,0.,target).astype('float32')),horizon,
                past_only_covariates=tensor(np.where(patient_mask,0.,patients).astype('float32')),
                past_future_covariates=tensor(known.astype('float32')),
                target_mask=tensor(target_mask),past_only_mask=tensor(patient_mask))
            output=result[:,:target.shape[1]].cpu().numpy()
        if output.shape!=(*target.shape[:2],horizon,9) or not np.isfinite(output).all(): raise ValueError('Invalid native forecast shape or nonfinite output')
        return np.sort(output,axis=-1)


def load_and_check_mps_model() -> tuple[MaskedTimesFM,dict]:
    """Load official weights on MPS; record masked-value invariance and resource use."""
    if not torch.backends.mps.is_available(): raise RuntimeError('MPS unavailable')
    started=time.perf_counter();f=TimesFM3Forecaster.from_pretrained('google/timesfm-3.0-pytorch',device='mps')
    adapter=MaskedTimesFM(f,'mps');rng=np.random.default_rng(20261008)
    y=rng.random((1,3,28)).astype('float32');p=rng.random((1,69,28)).astype('float32');z=rng.random((1,16,42)).astype('float32')
    y[:,:,4]=np.nan
    q=adapter.predict_masked(y,p,z,7)
    p2=p.copy();p2[:,:,:-7]=9999
    q2=adapter.predict_masked(y,p2,z,7)
    assert np.allclose(q,q2,atol=1e-5), 'Masked old patient values changed forecast'
    y2=y.copy();y2[:,1,-3:]+=20
    q3=adapter.predict_masked(y2,p,z,7)
    checks={'device':'mps','torch_version':torch.__version__,'load_and_check_seconds':time.perf_counter()-started,'shape':list(q.shape),'masked_old_patient_max_difference':float(np.max(abs(q-q2))),'neighbor_channel_effect_max':float(np.max(abs(q3[:,0]-q[:,0]))),'allocated_bytes':torch.mps.current_allocated_memory(),'native_target_mask':True,'high_level_interpolation_bypassed':True}
    return adapter,checks
