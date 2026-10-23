# msgfmt

Библиотека разметки сообщений (C++17, только стандартная библиотека). Спецификация: `docs/spec_part1.md`.

Состояние: этап 1.1 — инфраструктура, UTF-8 и классы символов. Парсер и CLI пока не реализованы
(`msgfmt` — заглушка).

## Сборка и тесты

```
cmake -S msgfmt -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
ctest --test-dir build
```

Опции: `-DMSGFMT_WERROR=ON` (предупреждения как ошибки), `-DMSGFMT_SANITIZE=ON` (ASan + UBSan).
Тест `contracts_lint` запускает `tools/check_contracts.py` (контракты и метки `covers:`).

## Состав

- `include/msgfmt/contract.hpp` — `ContractViolation`, `MSGFMT_EXPECTS` (работает и в Release).
- `include/msgfmt/utf8.hpp` — строгий декодер UTF-8, кодировщик, длина в единицах UTF-16.
- `include/msgfmt/charclass.hpp` — `is_whitespace`, `is_letter_or_digit` (табличные).
- `tests/test_framework.hpp` — собственный мини-харнесс (`TEST`, `CHECK`, `CHECK_EQ`, `CHECK_THROWS`).

## Принятые решения и ограничения

См. `docs/decisions.md`. Главное ограничение: `is_letter_or_digit` — приближение категорий L*/Nd;
комбинирующие знаки и ряд письменностей не учитываются (перечень в контракте функции).
