from __future__ import annotations

import json
import os
from pathlib import Path
import re


APPENDIX_HEADING = '\n## 부록 A. 단일기간 확보량 보조 분석\n'


def compose_forecast_and_policy_report(forecast_report: str, policy_report: str, forecast_directory: Path, policy_directory: Path) -> str:
    """Combine complete prediction and policy results, rebasing local artifact links."""
    forecast_body = re.split(r'\n## 7\. ', forecast_report, maxsplit=1)[0]
    forecast_body = re.sub(r'^# [^\n]+', '# 천안의료원 약품·물품 수량 예측과 주문 정책의 물류 효율 평가', forecast_body, count=1)
    if APPENDIX_HEADING in forecast_report:
        auxiliary = forecast_report.split(APPENDIX_HEADING, 1)[1]
    elif '### 이전 단일기간 확보량 보조 분석' in forecast_report:
        auxiliary = forecast_report.split('### 이전 단일기간 확보량 보조 분석', 1)[1].split('\n## 8. ', 1)[0].strip()
    else:
        raise ValueError('Original auxiliary analysis is missing; preserve executed results explicitly.')
    policy_body = policy_report.split('\n', 1)[1].strip()
    policy_body = policy_body.replace('[기존 품목 보고서](../boosting_two_items_20261007_01/report.md)', '앞 절의 예측 결과')
    policy_body = re.sub(r'^## ', '### ', policy_body, flags=re.MULTILINE)

    def rebase_artifact_link(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        if target.startswith(('https:', 'http:', '#')):
            return match.group(0)
        target_path = (policy_directory / target).resolve()
        if not target_path.exists():
            raise FileNotFoundError(target_path)
        relative_path = os.path.relpath(target_path, forecast_directory.resolve())
        return f'[{label}]({relative_path})'

    policy_body = re.sub(r'\[([^\]]*)\]\(([^)]+)\)', rebase_artifact_link, policy_body)
    storage = '''
## 8. 결과 저장과 검증

예측과 정책 비교의 방법·그림·수치는 위 본문에 모두 포함했다. 두 실행의 원점별 예측, 월별 모델, 정책 후보, 주문·입고·재고·비용 원장은 각각의 실행 폴더에 보존한다.

[예측 설정](config_snapshot.json), [예측 실행 manifest](manifest.json), [예측 지표](accuracy_metrics.parquet), [월별 예측 지표](monthly_metrics.parquet), [예측 검증](report_validation.json), [통합 문서 생성 기록](combined_report_sources.json).

주 분석은 예측 활용 대 미활용 **주문 정책**의 비교다. 아래 부록은 예측 방법 간 단일기간 확보량의 보조 분석으로, 주 정책 효과와 구분한다.
'''
    return forecast_body.rstrip() + '\n\n## 7. 예측 활용·미활용 주문 정책의 물류 효율 비교\n\n' + policy_body + '\n' + storage + APPENDIX_HEADING + auxiliary.strip() + '\n'


def combine_existing_forecast_and_policy_reports(forecast_directory: Path, policy_directory: Path) -> Path:
    """Write the single canonical report and record exact source artifact directories."""
    forecast_path = forecast_directory / 'report.md'
    policy_path = policy_directory / 'report.md'
    forecast_path.write_text(compose_forecast_and_policy_report(forecast_path.read_text(), policy_path.read_text(), forecast_directory, policy_directory))
    (forecast_directory / 'combined_report_sources.json').write_text(json.dumps({'canonical_report':str(forecast_path),'policy_directory':str(policy_directory),'forecast_directory':str(forecast_directory),'policy_report':str(policy_path),'contents':'prediction methods, curves, metrics; complete order-policy assumptions, formulas, curves, metrics, sensitivity; auxiliary analysis appendix'},ensure_ascii=False,indent=2))
    return forecast_path
