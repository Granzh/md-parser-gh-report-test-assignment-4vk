# vk-test

Учебное задание из трёх частей: C++-библиотека `msgfmt`, Go-служба `ghreport` (Docker),
Python-интеграционные тесты. Правила разработки — в [CLAUDE.md](CLAUDE.md).

## Структура репозитория

| Путь | Назначение |
|------|------------|
| `msgfmt/` | Часть 1: C++-библиотека (`include/msgfmt`, `src`, `tests`) |
| `service/` | Часть 2: Go-служба `ghreport` и Dockerfile |
| `api_tests/` | Часть 3: Python-интеграционные тесты |
| `docs/` | Спецификация, `decisions.md`, `WORKLOG.md` |
| `tools/` | Проверка окружения и линтер контрактов; тесты в `tools/tests/` |

## Быстрый старт

- Окружение: `python tools/check_env.py`
- Контракты и покрытие: `python tools/check_contracts.py tools --tests tools/tests --check-covers`
- Тесты инструментов: `python -m pytest tools/tests -q`
- Часть 1 (msgfmt): _заглушка_
- Часть 2 (ghreport): _заглушка_
- Часть 3 (api_tests): _заглушка_

## Затраченное время

| Этап | Время |
|------|-------|
| 0. Инфраструктура | |
| 1. msgfmt | |
| 2. ghreport | |
| 3. api_tests | |

## Использованные ИИ-инструменты

- Claude Code

## Принятые решения

См. [docs/decisions.md](docs/decisions.md).
