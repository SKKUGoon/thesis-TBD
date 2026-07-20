## Dataset overview

| Characteristic | Epurun | Seosong |
|---|---|---|
| Data source | Patient-prescription extract | Inpatient, outpatient, and ward workbooks |
| Observation period | 2021-01-01 to 2024-12-31 | 2020-12-14 to 2025-12-31 |
| Unit of analysis | Calendar day | Calendar day |
| Daily observations | 1,461 | 1,844 |
| Patient-feature composition | 2 sex + 9 age + 27 diagnosis counts | 2 sex + 9 age + 27 diagnosis counts |
| Patient-side covariates | 38 | 38 |
| Calendar covariates used by the model | 7 | 0 |
| Total model covariates | 45 | 38 |
| Medication target identifier | ATC-mapped medication code | Hospital order code |
| Medication targets | 248 | 3,126 |
| Target value | Distinct patients per medication per day | Distinct patients per medication per day |
| Zero-valued cells in the target matrix | 49.19% | 94.12% |
| Median target active-day rate | 47.36% | 0.92% |

## Sex and age-group patient-day counts

| Measure | Category | Epurun | Seosong |
|---|---|---|---|
| Sex | Female | 105,481 | 51,123 |
| Sex | Male | 44,534 | 50,699 |
| Age group | 0-9 | 0 | 21,207 |
| Age group | 10-19 | 0 | 3,588 |
| Age group | 20-29 | 0 | 6,648 |
| Age group | 30-39 | 14 | 6,130 |
| Age group | 40-49 | 4,482 | 9,404 |
| Age group | 50-59 | 3,420 | 19,144 |
| Age group | 60-69 | 12,737 | 21,084 |
| Age group | 70-79 | 30,288 | 10,641 |
| Age group | 80+ | 99,074 | 3,975 |

## Diagnosis-group counts

| KCD group | Epurun prescription-linked diagnosis records | Seosong patient-day diagnosis-category counts |
|---|---|---|
| A | 22,350 | 201 |
| B | 4 | 1,020 |
| C | 61,874 | 1,230 |
| D | 1,875 | 1,895 |
| E | 29,549 | 10,986 |
| F | 289,559 | 4,064 |
| G | 511,905 | 30,495 |
| H | 1,289 | 1,102 |
| I | 208,898 | 29,153 |
| J | 47,064 | 2,500 |
| K | 45,930 | 5,429 |
| L | 13,417 | 1,139 |
| M | 246,477 | 22,484 |
| N | 34,041 | 8,581 |
| O | 0 | 43 |
| P | 0 | 5,195 |
| Q | 0 | 6,218 |
| R | 30,522 | 29,219 |
| S | 407,340 | 7,959 |
| T | 33,173 | 1,373 |
| U | 16,262 | 512 |
| V | 0 | 0 |
| W | 0 | 5 |
| X | 0 | 0 |
| Y | 0 | 0 |
| Z | 3,526 | 12,932 |
| Other | 1,160,303 | 15,369 |

## Weekday visit distribution and Tukey boxplot values

| Hospital | Weekday | Days | Mean visits | Lower whisker | Q1 | Median | Q3 | Upper whisker | Outlier days |
|---|---|---|---|---|---|---|---|---|---|
| Epurun | Monday | 209 | 102.51 | 78.00 | 95.00 | 103.00 | 110.00 | 127.00 | 0 |
| Epurun | Tuesday | 209 | 102.65 | 77.00 | 96.00 | 103.00 | 110.00 | 127.00 | 0 |
| Epurun | Wednesday | 208 | 102.77 | 79.00 | 95.75 | 103.00 | 110.00 | 125.00 | 0 |
| Epurun | Thursday | 208 | 102.77 | 79.00 | 96.00 | 103.00 | 110.00 | 125.00 | 0 |
| Epurun | Friday | 209 | 103.04 | 78.00 | 96.00 | 103.00 | 111.00 | 126.00 | 0 |
| Epurun | Saturday | 209 | 102.78 | 79.00 | 95.00 | 103.00 | 110.00 | 127.00 | 0 |
| Epurun | Sunday | 209 | 102.24 | 78.00 | 94.00 | 102.00 | 109.00 | 127.00 | 0 |
| Seosong | Monday | 264 | 71.35 | 0.00 | 18.50 | 67.00 | 104.50 | 207.00 | 0 |
| Seosong | Tuesday | 264 | 69.12 | 0.00 | 25.75 | 67.00 | 94.25 | 184.00 | 3 |
| Seosong | Wednesday | 264 | 71.48 | 0.00 | 24.25 | 65.50 | 106.25 | 219.00 | 2 |
| Seosong | Thursday | 263 | 70.71 | 0.00 | 22.50 | 66.00 | 101.00 | 191.00 | 3 |
| Seosong | Friday | 263 | 77.48 | 0.00 | 31.00 | 72.00 | 115.00 | 237.00 | 3 |
| Seosong | Saturday | 263 | 20.60 | 0.00 | 9.00 | 21.00 | 31.00 | 56.00 | 0 |
| Seosong | Sunday | 263 | 5.61 | 0.00 | 0.00 | 1.00 | 9.50 | 23.00 | 10 |

## Metric definitions and count bases

| Metric | Definition | Epurun | Seosong |
|---|---|---|---|
| Visit count used in weekday table | Female + Male daily counts; counts sex-classifiable patient-days, not period-unique patients | Same definition | May exclude records whose sex could not be derived |
| Boxplot whiskers | Smallest and largest observations within Q1 - 1.5 x IQR and Q3 + 1.5 x IQR | Tukey rule | Tukey rule |
| Zero-valued cells in target matrix | Day-medication combinations with zero distinct patients; zero is observed no activity, not missing data | 178,225 / 362,328 (49.19%) | 5,425,362 / 5,764,344 (94.12%) |
| Median target active-day rate | For each medication, active days divided by study days; reported value is the median across medications | 692 / 1,461 days (47.36%) | 17 / 1,844 days (0.92%) |
| Diagnosis count basis | Hospital-specific executed preprocessing contract | Prescription-linked primary + secondary diagnosis records | Distinct patient-day-diagnosis-category counts |
