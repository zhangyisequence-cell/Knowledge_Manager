# Synthetic fixture

`legacy.xls` was generated with xlwt 1.3.0 for XLS parser acceptance. It has a 预算 sheet with headers 项目/金额/日期, a 资料采购 row (12800.5, 2026-09-18), a 免费材料 row (0, FALSE), and a 说明 sheet stating that it contains synthetic test data. It contains no user documents or credentials. xlwt is not a runtime or CI dependency; tests read the fixed file with the real parser.
