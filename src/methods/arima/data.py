from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


@dataclass(frozen=True)
class DrugGroupMember:
    item_code: str
    hospital_name: str
    source_product_id: str
    product_name: str
    manufacturer: str
    atc_code: str
    ingredient: str
    strength: str
    dosage_form: str
    base_unit: str
    route_basis: str
    metadata_group_key: str
    mapping_basis: str


@dataclass(frozen=True)
class MetadataGroupSelection:
    members: tuple[DrugGroupMember, ...]
    mapping_rows: tuple[dict[str, object], ...]
    candidate_rows: tuple[dict[str, object], ...]


def calculate_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_product_text(value: str) -> str:
    normalized = unicodedata.normalize('NFKC', value).upper()
    for source, target in [('마이크로그램', 'MCG'), ('밀리그램', 'MG'), ('밀리그람', 'MG'), ('그램', 'G'), ('그람', 'G'), ('캅셀', '캡슐')]:
        normalized = normalized.replace(source, target)
    normalized = re.sub(r'\([^)]*\)|\[[^]]*\]', '', normalized)
    return re.sub(r'[\s_/,]', '', normalized)


def extract_brand_and_form(value: str) -> tuple[str, str] | None:
    match = re.match(r'^(.+?)(캡슐|주사액|주사|시럽|크림|연고|과립|정|산|액)', normalize_product_text(value))
    if match is None:
        return None
    return match.group(1) + match.group(2), match.group(2)


def extract_strength(value: str) -> str | None:
    match = re.search(r'(\d+(?:\.\d+)?)(MCG|MG|G|ML)', normalize_product_text(value))
    if match is None:
        return None
    return f'{Decimal(match.group(1)).normalize():f}{match.group(2)}'


def select_single_group_from_metadata(master_rows: list[dict[str, str]], catalog_rows: list[dict[str, str]]) -> MetadataGroupSelection:
    catalog_by_brand: dict[str, list[dict[str, str]]] = defaultdict(list)
    for product in catalog_rows:
        brand_form = extract_brand_and_form(product['제품명'])
        if brand_form is not None and product['허가일'][:4] <= '2022':
            catalog_by_brand[brand_form[0]].append(product)
    initial_master: dict[str, list[dict[str, str]]] = defaultdict(list)
    for record in master_rows:
        if record['일자'][:4] <= '2022':
            initial_master[record['품목코드']].append(record)
    groups: dict[str, list[DrugGroupMember]] = defaultdict(list)
    mapping_rows: list[dict[str, object]] = []
    for item_code, records in sorted(initial_master.items()):
        names = {extract_brand_and_form(record['품목명']) for record in records}
        units = {record['단위'].strip() for record in records if record['단위'].strip()}
        hospital_strengths = {extract_strength(record['품목명']) for record in records}
        brand_form = next(iter(names)) if len(names) == 1 else None
        strength = next(iter(hospital_strengths)) if len(hospital_strengths) == 1 else None
        matches = [] if brand_form is None else catalog_by_brand.get(brand_form[0], [])
        if strength is not None:
            matches = [product for product in matches if extract_strength(product['함량']) == strength]
        profiles: dict[tuple[str, ...], dict[str, str]] = {}
        for product in matches:
            code_match = re.match(r'^([A-Z]\d{2}[A-Z]{2}\d{2})(?:\s|\(|$)', product['atc_code'])
            product_strength = extract_strength(product['함량'])
            if code_match is None or product_strength is None or not product['주성분'].strip():
                continue
            profile = (product['item_seq'], code_match[1], product['주성분'], product_strength, product['업체명'])
            profiles[profile] = product
        reason = 'matched_unique_product'
        if brand_form is None or strength is None:
            reason = 'missing_or_ambiguous_hospital_product_or_strength'
        elif len(profiles) != 1:
            reason = 'missing_or_ambiguous_external_product'
        elif len(units) != 1:
            reason = 'missing_or_ambiguous_hospital_base_unit'
        elif brand_form[1] not in {'정', '캡슐'} or next(iter(units)) != brand_form[1]:
            reason = 'unsupported_or_mismatched_basic_unit'
        mapping_rows.append({'item_code': item_code, 'hospital_name': records[0]['품목명'], 'mapping_status': reason, 'external_profile_count': len(profiles)})
        if reason != 'matched_unique_product':
            continue
        profile, product = next(iter(profiles.items()))
        # Same product identity establishes a common formulation; route is not independently asserted.
        group_key = '|'.join([profile[1], profile[2], profile[3], brand_form[1], next(iter(units)), 'same_product:' + profile[0]])
        groups[group_key].append(DrugGroupMember(item_code, records[0]['품목명'], profile[0], product['제품명'], profile[4], profile[1], profile[2], profile[3], brand_form[1], next(iter(units)), 'same_product_identity; route_unverified', group_key, 'unique_brand_form_strength_and_explicit_hospital_unit'))
    candidate_rows = [{'metadata_group_key': key, 'member_count': len(members), 'eligible': 2 <= len(members) <= 20} for key, members in sorted(groups.items())]
    eligible_keys = [key for key, members in sorted(groups.items()) if 2 <= len(members) <= 20]
    if not eligible_keys:
        raise ValueError('No comparable 2–20-code metadata group; consumption patterns cannot be used to manufacture one.')
    return MetadataGroupSelection(tuple(sorted(groups[eligible_keys[0]], key=lambda member: member.item_code)), tuple(mapping_rows), tuple(candidate_rows))


def build_and_write_group_dataset(original_directory: Path, catalog_path: Path, output_directory: Path, zero_semantics_confirmed: bool = False) -> tuple[pd.Series, dict[str, object]]:
    output_directory.mkdir(parents=True, exist_ok=False)
    master_path = next(path for path in original_directory.glob('*.parquet') if '약품' in unicodedata.normalize('NFC', path.name))
    master_columns = ['일자', '품목코드', '품목명', '단위', '규격']
    master_rows = pq.read_table(master_path, columns=master_columns).to_pylist()
    catalog_rows = pq.read_table(catalog_path).to_pylist()
    selection = select_single_group_from_metadata(master_rows, catalog_rows)
    group_rows = [asdict(member) for member in selection.members]
    pd.DataFrame(group_rows).to_parquet(output_directory / 'frozen_single_group.parquet', index=False)
    pd.DataFrame(selection.mapping_rows).to_parquet(output_directory / 'drug_metadata_mapping.parquet', index=False)
    pd.DataFrame(selection.candidate_rows).to_parquet(output_directory / 'group_candidate_audit.parquet', index=False)
    codes = [member.item_code for member in selection.members]
    counts: Counter[tuple[pd.Timestamp, str]] = Counter()
    sources: list[dict[str, object]] = []
    for path in sorted(original_directory.glob('20*.parquet')):
        selected_rows = 0
        for batch in pq.ParquetFile(path).iter_batches(batch_size=65536, columns=['품목코드', '의약품사용일시']):
            selected = batch.filter(pc.is_in(batch.column(0), value_set=pa.array(codes)))
            for code, event_time in zip(selected.column(0).to_pylist(), selected.column(1).to_pylist()):
                date_digits = re.sub(r'\D', '', event_time)[:8]
                date = pd.to_datetime(date_digits, format='%Y%m%d')
                if not pd.Timestamp('2020-01-01') <= date <= pd.Timestamp('2024-12-31'):
                    raise ValueError('Selected event outside source study range.')
                counts[(date, code)] += 1
                selected_rows += 1
        sources.append({'path': str(path), 'sha256': calculate_file_sha256(path), 'selected_rows': selected_rows})
    calendar = pd.date_range('2020-01-01', '2024-12-31', freq='D')
    daily_items = pd.DataFrame([{ 'date': date, 'item_code': code, 'recorded_count': counts[(date, code)], 'consumption': float(counts[(date, code)]) if counts[(date, code)] > 0 or zero_semantics_confirmed else float('nan'), 'observation_status': 'record_present' if counts[(date, code)] > 0 else ('confirmed_zero' if zero_semantics_confirmed else 'unobserved_no_record')} for date in calendar for code in codes])
    daily_group = daily_items.groupby('date')['recorded_count'].sum().astype(float)
    group_series = daily_group.copy()
    if not zero_semantics_confirmed:
        group_series = group_series.where(group_series.gt(0))
    group_series.index.freq = 'D'
    group_series.name = 'group_recorded_consumption'
    assert int(daily_items['recorded_count'].sum()) == sum(source['selected_rows'] for source in sources)
    group_frame = pd.DataFrame({'date': calendar, 'recorded_group_count': daily_group.values, 'consumption': group_series.values, 'observation_status': ['positive_recorded_total_completeness_unverified' if value > 0 and not zero_semantics_confirmed else ('observed' if zero_semantics_confirmed else 'unobserved_no_record') for value in daily_group.values]})
    daily_items.to_parquet(output_directory / 'daily_item_consumption.parquet', index=False)
    group_frame.to_parquet(output_directory / 'daily_group_consumption.parquet', index=False)
    group_frame[['date', 'observation_status']].to_parquet(output_directory / 'observation_masks.parquet', index=False)
    metadata = {'group_members': group_rows, 'group_key': selection.members[0].metadata_group_key, 'group_hash': calculate_file_sha256(output_directory / 'frozen_single_group.parquet'), 'grouping_uses_consumption': False, 'grouping_type': 'retrospective_metadata_grouping_same_product_administrative_codes', 'historical_metadata_availability': 'unverified', 'clinical_generic_substitution_verified': False, 'route_independently_verified': False, 'selected_rows': int(daily_items['recorded_count'].sum()), 'missing_group_days': int(group_series.isna().sum()), 'zero_semantics_confirmed': zero_semantics_confirmed, 'target_interpretation': 'recorded export counts under user one-row-one-unit contract; extraction completeness unverified unless confirmed', 'sources': sources + [{'path': str(master_path), 'sha256': calculate_file_sha256(master_path)}, {'path': str(catalog_path), 'sha256': calculate_file_sha256(catalog_path)}], 'original_values_changed': False, 'imputation_applied': False}
    (output_directory / 'manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    return group_series, metadata
