# msgfmt

Библиотека разметки сообщений (C++17, только стандартная библиотека). Спецификация: `docs/spec_part1.md`.

Состояние: этапы 1.1-1.2 — инфраструктура, UTF-8, классы символов и ядро парсера. CLI и JSON пока не
реализованы (`msgfmt` — заглушка).

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
- `include/msgfmt/parser.hpp` — `parse(u32string_view)`, `parse_utf8(string_view)`, `Style`, `Range`, `ParseResult`.
- `include/msgfmt/parser_internal.hpp` — `msgfmt::detail`: `find_closer`, `try_open_marker`, `parse_link`, `parse_slice`, `sort_ranges` (для прямого тестирования).
- `tests/test_framework.hpp` — собственный мини-харнесс (`TEST`, `CHECK`, `CHECK_EQ`, `CHECK_THROWS`).

## Парсер: ключевые решения

Рекурсивный разбор срезов: содержимое пары маркеров разбирается только внутри пары, поэтому частичные пересечения
диапазонов невозможны. Подробности и тесты — в `docs/decisions.md`. Коротко:

- закрывающий маркер ищется в сыром тексте (экранированное пропускается, code-span не учитывается), ближайший с непробельным символом перед ним;
- пустое содержимое (`****`, ``` `` ```, `__`) — литералы; диапазонов длины 0 нет;
- `**жирный _и курсив_**` даёт italic(7,8) (по правилам), `**жирный _и_ курсив**` — italic(7,1);
- `parse` тотальна; недопустимые скалярные значения заменяются на U+FFFD;
- худший случай по времени — O(n²) на длинных цепочках незакрытых маркеров, глубина рекурсии не больше 4.

## Принятые решения и ограничения

См. `docs/decisions.md`. Главное ограничение: `is_letter_or_digit` — приближение категорий L*/Nd;
комбинирующие знаки и ряд письменностей не учитываются (перечень в контракте функции).
