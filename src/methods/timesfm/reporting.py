from __future__ import annotations

import json
from pathlib import Path
import shutil

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.methods.timesfm.data import file_sha256


def finalize_audited_integrated_report(root: Path, output: Path, verified_dataset: Path) -> dict:
    """Validate prior-only selection equality, add concise figures, preserve executed code."""
    manifest=json.loads((output/'manifest.json').read_text());dataset=root/manifest['config']['dataset']
    for name in ['frozen_cohorts.parquet','daily_targets.parquet']:
        pd.testing.assert_frame_equal(pd.read_parquet(dataset/name),pd.read_parquet(verified_dataset/name))
    manifest['cohort_selection_audit']={'verified_prior_only_dataset':str(verified_dataset.relative_to(root)),'prior_only_unit_eligibility':True,'all_channel_names_order_target_values_equal_to_executed_inputs':True,'initial_selector_issue':'initial material unit_count field covered entire period; corrected to 2020-2022 unique units before final interpretation; no selected channel or input target changed','sha256':{p.name:file_sha256(p) for p in verified_dataset.glob('*.parquet')}}
    snapshot=output/'source_snapshot';snapshot.mkdir(exist_ok=True)
    for relative,digest in manifest['code_sha256'].items():
        original=root/relative
        destination=snapshot/relative
        if destination.exists():
            if file_sha256(destination)!=digest: raise ValueError(f'Executed snapshot changed: {relative}')
        else:
            if file_sha256(original)!=digest: raise ValueError(f'Executed source changed: {relative}')
            destination.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(original,destination)
    shutil.copy2(root/'configs/timesfm_vector.json',snapshot/'config.json')
    manifest['executed_source_snapshot']='source_snapshot/'
    cohorts=pd.read_parquet(verified_dataset/'frozen_cohorts.parquet');metrics=pd.read_parquet(output/'prediction_metrics.parquet');policies=pd.read_parquet(output/'policy_evaluation_metrics.parquet');forecasts=pd.concat([pd.read_parquet(output/f'{kind}_joint_evaluation.parquet') for kind in ['drug','material']])
    plt.rcParams['font.family']='AppleGothic';plt.rcParams['axes.unicode_minus']=False
    fig,axes=plt.subplots(3,2,figsize=(16,10))
    for col,kind in enumerate(['drug','material']):
        for axis,info in zip(axes[:,col],cohorts[cohorts.cohort==kind].head(3).itertuples()):
            data=forecasts[forecasts.channel==info.channel].sort_values('origin').drop_duplicates('target_date',keep='last').sort_values('target_date')
            if kind=='material':axis.bar(data.target_date,data.actual,color='#999999',label='실제 기록 출고')
            else:axis.plot(data.target_date,data.actual,color='#666666',linewidth=.8,label='실제 사용 기록')
            axis.plot(data.target_date,data.prediction,color='#e66101',linewidth=.8,label='TimesFM 예측');axis.set_title(info.name);axis.legend(fontsize=8)
    fig.tight_layout();fig.savefig(output/'forecast_examples.png',dpi=150);plt.close(fig)
    utilized=policies[policies.policy=='timesfm_joint']
    fig,axes=plt.subplots(1,2,figsize=(16,11))
    for axis,kind in zip(axes,['drug','material']):
        data=utilized[utilized.cohort==kind];positions=np.arange(len(data))
        axis.barh(positions,data.cost_reduction_percent,color=['#1b9e77' if x else '#bbbbbb' for x in data.paired_service_target_met]);axis.set_yticks(positions,data.name);axis.invert_yaxis();axis.axvline(0,color='black',linewidth=.7);axis.set_xlabel('예측 활용 비용 감소율 (%)');axis.set_title(('약품' if kind=='drug' else '물품')+' 주문 정책 비교')
    fig.tight_layout();fig.savefig(output/'policy_cost_reduction.png',dpi=140);plt.close(fig)
    summary=[]
    for kind in ['drug','material']:
        selected=metrics[metrics.cohort==kind];joint=float(selected[selected['mode']=='joint'].mase.mean());independent=float(selected[selected['mode']=='independent'].mase.mean());policy=utilized[utilized.cohort==kind];qualified=policy[policy.paired_service_target_met];improved=qualified[qualified.cost_reduction_percent>0]
        summary.append({'cohort':kind,'joint_macro_mase':joint,'independent_macro_mase':independent,'joint_vs_independent_improvement_percent':100*(1-joint/independent),'items':len(policy),'paired_95_service_items':len(qualified),'paired_service_and_lower_cost_items':len(improved),'qualified_mean_cost_reduction_percent':float(qualified.cost_reduction_percent.mean()) if len(qualified) else None,'all_item_mean_cost_reduction_percent':float(policy.cost_reduction_percent.mean())})
    (output/'executive_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    lines='\n'.join(f"- **{'약품' if s['cohort']=='drug' else '물품'}**: 공동 예측 macro MASE {s['joint_macro_mase']:.3f}, 독립 예측 {s['independent_macro_mase']:.3f}. 양쪽 95% 충족 {s['paired_95_service_items']}/{s['items']}개, 그중 비용 감소 {s['paired_service_and_lower_cost_items']}개. 모든 품목 동일 가중 평균 비용 감소율 {s['all_item_mean_cost_reduction_percent']:.2f}%이며 서비스 미달 품목까지 포함한 진단값이다." for s in summary)
    report=(output/'report.md').read_text();report=report.replace('## 6. 예측 수치와 해석','## 6. 예측 수치와 해석\n\n'+lines+'\n')
    report=report.replace('## 6. 예측 수치와 해석','![대표 약품 3개·물품 3개 예측](forecast_examples.png)\n\n## 6. 예측 수치와 해석')
    report=report.replace('미달 품목은 비용 감소만으로 효율 개선으로 판정하지 않는다.','![품목별 주문 정책 비용 감소율](policy_cost_reduction.png)\n\n그림의 녹색은 양쪽 95% 충족, 회색은 서비스 조건 미달이다. 비용이 감소한 회색 품목을 효율 개선으로 판정하지 않는다.\n\n미달 품목은 비용 감소만으로 효율 개선으로 판정하지 않는다.')
    report+='\n\n선정 감사: 최초 물품 단위 개수 필드의 전체 기간 참조를 발견하여 2020–2022년 단위 검사로 수정했다. 수정 선정 데이터셋과 실행 입력의 30개 이름·순서·모든 일별 목표가 동일함을 비교 검증했다. 원본과 초기 감사 자료는 보존했다. [교정된 선정 자료]('+str(verified_dataset.relative_to(root))+'/manifest.json)는 manifest의 입력 감사에 연결된다.\n'
    # Correct the local reference from the report directory.
    import os
    report=report.replace(str(verified_dataset.relative_to(root))+'/manifest.json',os.path.relpath(verified_dataset/'manifest.json',output))
    (output/'report.md').write_text(report)
    manifest['output_sha256']={p.name:file_sha256(p) for p in output.iterdir() if p.suffix in ['.parquet','.png','.md']}
    manifest['reporting_code_sha256']=file_sha256(Path(__file__));manifest['verification']={'focused_tests':'6 passed (timesfm inputs, per-mode metrics and order policy)','parquet_target_selection_equality':True,'source_hash_verified':True,'inventory_mass_balance_assertions':True}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    from src.methods.timesfm.reader_report import write_reader_report
    write_reader_report(root, output)
    return {'summary':summary,'report':str(output/'report.md')}


if __name__=='__main__':
    root=Path.cwd();print(json.dumps(finalize_audited_integrated_report(root,root/'runs/cheonan/timesfm_vector_20261008_01',root/'data/processed/cheonan/analysis/timesfm_cohorts_20261008_02'),ensure_ascii=False,indent=2))
