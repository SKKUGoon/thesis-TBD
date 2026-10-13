from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import unicodedata

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


def file_sha256(path: Path) -> str:
    """Read file bytes to calculate provenance without decoding patient rows."""
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''): digest.update(block)
    return digest.hexdigest()


def build_and_write_frozen_cohorts(root: Path, output: Path) -> tuple[pd.DataFrame,pd.DataFrame]:
    """Select prior-period products and write aggregate targets; never alter originals."""
    output.mkdir(parents=True,exist_ok=False)
    original=root/'data/processed/cheonan/original'
    master_path=next(p for p in original.glob('*.parquet') if '약품' in unicodedata.normalize('NFC',p.name))
    master=pd.read_parquet(master_path,columns=['일자','품목코드','품목명','표준코드','단위'])
    master['date']=pd.to_datetime(master['일자'],errors='coerce')
    prior=master[master.date.between('2020-01-01','2022-12-31')].copy()
    mapping={};metadata={};audit=[]
    for code,frame in prior.groupby('품목코드',sort=True):
        standards=sorted(set(str(x).strip() for x in frame['표준코드'] if str(x).strip()))
        # Exact single standardized product identifier only; no name-based guessed merging.
        key='standard:'+standards[0] if len(standards)==1 else 'code:'+code
        latest=frame.sort_values('date').iloc[-1]
        mapping[code]=key
        metadata.setdefault(key,{'name':latest['품목명'],'unit':latest['단위'] or '약품 기본 단위(기관 환산 미확인)','codes':[]})['codes'].append(code)
        audit.append({'code':code,'product_key':key,'name':latest['품목명'],'standard_codes':json.dumps(standards),'mapping_status':'single prior standard code' if len(standards)==1 else 'unresolved separate administrative code'})
    counters=Counter();paths=list(sorted(original.glob('20*.parquet')))
    for path in paths:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=65536,columns=['품목코드','의약품사용일시']):
            frame=batch.to_pandas()
            frame['date']=pd.to_datetime(frame['의약품사용일시'].str[:10],errors='coerce')
            frame['product']=frame['품목코드'].map(mapping)
            counts=frame.dropna(subset=['date','product']).groupby(['date','product']).size()
            counters.update(counts.to_dict())
    daily=pd.Series(counters,dtype=float).rename('count').rename_axis(['date','product']).reset_index()
    ranking=daily[daily.date.between('2020-01-01','2022-12-31')].groupby('product')['count'].sum().reset_index().sort_values(['count','product'],ascending=[False,True])
    # Require resolved product identity to count products rather than unknown code aliases.
    ranking['eligible']=ranking['product'].str.startswith('standard:')
    selected=ranking[ranking.eligible].head(20)
    cohort=[];targets={};calendar=pd.date_range('2020-01-01','2024-12-31')
    for rank,row in enumerate(selected.itertuples(),1):
        info=metadata[row.product];key=f'drug_{rank:02}'
        cohort.append({'channel':key,'cohort':'drug','name':info['name'],'unit':info['unit'],'source_key':row.product,'codes':json.dumps(info['codes']), 'selection_score':row.count,'rank':rank,'selection_basis':'2020-2022 usage row count; prior single standardized code'})
        targets[key]=daily[daily['product']==row.product].set_index('date')['count'].reindex(calendar)
    movements_path=root/'data/processed/cheonan/diagnostics/material_audit_20261006/unambiguous_recorded_daily_movements.parquet'
    catalogue_path=movements_path.parent/'item_catalogue_and_quality.parquet'
    moves=pd.read_parquet(movements_path);catalog=pd.read_parquet(catalogue_path)
    training=moves[moves.date.between('2020-01-01','2022-12-31')]
    mrank=training[training['출고수량']>0].groupby('품목코드').date.nunique().rename('score').reset_index()
    mrank=mrank.merge(catalog,left_on='품목코드',right_on='item_code').sort_values(['score','item_code'],ascending=[False,True])
    mrank['prior_unit_count']=mrank['item_code'].map(training.groupby('품목코드')['단위'].nunique())
    mrank['eligible']=(mrank.training_structural_status=='명칭·단위·행 구조 점검 통과')&(mrank.prior_unit_count==1)
    chosen=mrank[mrank.eligible].head(10)
    for rank,row in enumerate(chosen.itertuples(index=False),1):
        code=row.item_code;frame=moves[moves['품목코드']==code]
        prior_frame=training[training['품목코드']==code]
        if frame.date.duplicated().any(): raise ValueError('Unresolved multiple material movements on same date')
        key=f'material_{rank:02}'
        names=json.loads(row.training_names);name=names[0] if names else row.display_name
        cohort.append({'channel':key,'cohort':'material','name':name,'unit':str(prior_frame['단위'].iloc[0]),'source_key':code,'codes':json.dumps([code]),'selection_score':row.score,'rank':rank,'selection_basis':'2020-2022 positive issue recorded days; prior structural eligibility'})
        targets[key]=frame.set_index('date')['출고수량'].reindex(calendar)
    cohort_frame=pd.DataFrame(cohort);target_frame=pd.DataFrame(targets,index=calendar).rename_axis('date')
    cohort_frame.to_parquet(output/'frozen_cohorts.parquet',index=False);target_frame.to_parquet(output/'daily_targets.parquet')
    ranking.to_parquet(output/'drug_candidate_ranking.parquet',index=False);mrank.to_parquet(output/'material_candidate_ranking.parquet',index=False)
    pd.DataFrame(audit).to_parquet(output/'prior_product_mapping.parquet',index=False)
    sources=[master_path,movements_path,catalogue_path,*paths]
    manifest={'plan':'v1.14','selection_end':'2022-12-31','selection_uses_future':False,'target_no_record':'NaN, not zero demand','duplicates_removed':False,'drug_product_identity':'single standardized code in prior master; clinical interchangeability not inferred','availability':'event date plus one day scenario; actual source availability unverified','sources':{str(p.relative_to(root)):file_sha256(p) for p in sources},'output_sha256':{p.name:file_sha256(p) for p in output.glob('*.parquet')},'shape':target_frame.shape,'channels':cohort_frame.to_dict('records')}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(cohort_frame[['cohort','name','selection_score']].to_string(index=False),flush=True)
    return cohort_frame,target_frame


if __name__=='__main__':
    root=Path.cwd();build_and_write_frozen_cohorts(root,root/'data/processed/cheonan/analysis/timesfm_cohorts_20261008_02')
