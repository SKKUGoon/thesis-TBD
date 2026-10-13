from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import holidays
import numpy as np
import pandas as pd


@dataclass(frozen=True)
class KnownCalendar:
    holiday_dates: np.ndarray
    holiday_available_dates: np.ndarray
    policy_effective_dates: np.ndarray
    policy_available_dates: np.ndarray
    policy_values: np.ndarray


ANNUAL = {
    2020: ('2019-06-03', 'https://eiec.kdi.re.kr/policy/materialView.do?num=189080&topic='),
    2021: ('2020-06-15', 'https://www.korea.kr/news/policyNewsView.do?newsId=148873429'),
    2022: ('2021-08-11', 'https://eiec.kdi.re.kr/policy/materialView.do?num=216956'),
    2023: ('2022-06-12', 'https://www.korea.kr/briefing/pressReleaseView.do?newsId=156511232'),
    2024: ('2023-06-22', 'https://eiec.kdi.re.kr/policy/materialView.do?num=239970'),
}
SPECIAL = {
    '2020-08-17': ('2020-07-21', 'https://www.korea.kr/news/policyNewsView.do?newsId=148874895'),
    '2021-08-16': ('2021-08-03', 'https://www.korea.kr/briefing/presidentView.do?newsId=148891132'),
    '2021-10-04': ('2021-08-03', 'https://www.korea.kr/briefing/presidentView.do?newsId=148891132'),
    '2021-10-11': ('2021-08-03', 'https://www.korea.kr/briefing/presidentView.do?newsId=148891132'),
    '2023-05-29': ('2023-05-02', 'https://www.korea.kr/briefing/stateCouncilView.do?newsId=148914550'),
    '2023-10-02': ('2023-09-05', 'https://www.korea.kr/news/policyNewsView.do?newsId=148919801'),
    '2024-10-01': ('2024-09-03', 'https://www.korea.kr/news/policyNewsView.do?newsId=156648663'),
}


def build_and_write_reference_calendar(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for year in range(2020, 2025):
        for day, name in sorted(holidays.KR(years=year, language='ko').items()):
            announcement, source = SPECIAL.get(str(day), ANNUAL[year])
            rows.append({'date': pd.Timestamp(day), 'name': name, 'announced_date': pd.Timestamp(announcement), 'available_at': pd.Timestamp(announcement) + pd.Timedelta(days=1), 'source': source})
    frame = pd.DataFrame(rows)
    expected_red_days = {2020:68, 2021:67, 2022:67, 2023:69, 2024:69}
    for year in range(2020, 2025):
        calendar = pd.date_range(f'{year}-01-01', f'{year}-12-31')
        count = int(((calendar.dayofweek == 6) | calendar.isin(frame.date)).sum())
        assert count == expected_red_days[year]
    frame.to_parquet(output/'korean_public_holidays.parquet',index=False)
    policy = pd.DataFrame([
        {'effective_date':'2020-02-29','announced_date':'2020-02-29','value':1,'source':'https://www.korea.kr/briefing/policyBriefingView.do?newsId=156377930'},
        {'effective_date':'2022-04-18','announced_date':'2022-04-15','value':0,'source':'https://www.korea.kr/briefing/policyBriefingView.do?newsId=148900782'},
    ])
    for column in ['effective_date','announced_date']:
        policy[column]=pd.to_datetime(policy[column])
    policy['available_at']=policy.announced_date+pd.Timedelta(days=1)
    policy.to_parquet(output/'distancing_policy_events.parquet',index=False)
    (output/'manifest.json').write_text(json.dumps({'source_type':'government calendar releases and announcement dates; holidays library generates dates verified against yearly red-day totals', 'holidays_version':holidays.__version__, 'annual_sources':ANNUAL,'amendment_sources':SPECIAL,'official_KCD_source':'https://sri.kostat.go.kr/boardDownload.es?bid=12030&list_no=428825&seq=1', 'announcement_availability':'conservative next-day midnight; no unverified exact intraday timestamps', 'policy_definition':'research broad distancing response campaign 2020-02-29 to 2022-04-17; not biological epidemic dates', 'hospital_purchasing_or_switch_policy_used':False},ensure_ascii=False,indent=2))


def read_known_calendar(reference: Path) -> KnownCalendar:
    holiday = pd.read_parquet(reference/'korean_public_holidays.parquet')
    policy = pd.read_parquet(reference/'distancing_policy_events.parquet').sort_values('effective_date')
    return KnownCalendar(holiday.date.to_numpy(), holiday.available_at.to_numpy(), policy.effective_date.to_numpy(),policy.available_at.to_numpy(),policy.value.to_numpy())


def calculate_known_calendar_features(origins: pd.DatetimeIndex, targets: pd.DatetimeIndex, calendar: KnownCalendar) -> dict[str,np.ndarray]:
    origin_values=origins.to_numpy()
    target_values=targets.to_numpy()
    named = np.zeros(len(origins),dtype=bool)
    before = named.copy()
    after = named.copy()
    for day, available in zip(calendar.holiday_dates,calendar.holiday_available_dates):
        known=origin_values>=available
        named |= known & (target_values==day)
        before |= known & (target_values+np.timedelta64(1,'D')==day)
        after |= known & (target_values-np.timedelta64(1,'D')==day)
    policy_target=np.zeros(len(origins),dtype=np.float32)
    policy_last=np.zeros(len(origins),dtype=np.float32)
    for effective,available,value in zip(calendar.policy_effective_dates,calendar.policy_available_dates,calendar.policy_values):
        known=origin_values>=available
        policy_target[known & (target_values>=effective)]=value
        policy_last[known & (origin_values-np.timedelta64(1,'D')>=effective)]=value
    return {'target_public_holiday':(named | (targets.dayofweek==6)).astype(np.float32), 'target_named_holiday':named.astype(np.float32),'target_before_named_holiday':before.astype(np.float32),'target_after_named_holiday':after.astype(np.float32),'target_weekend':(targets.dayofweek>=5).astype(np.float32),'is_national_epidemic_distancing_response':policy_target,'last_day_distancing_response':policy_last}
