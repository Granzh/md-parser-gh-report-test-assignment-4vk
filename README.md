# vk-test

Учебное задание из трёх частей:

1. **msgfmt** — библиотека и утилита на C++17 (только стандартная библиотека): разбор подмножества Markdown в пару
   «чистый текст + диапазоны форматирования» с позициями в кодовых единицах UTF-16.
2. **ghreport** — служба на Go (только `net/http` и stdlib, без SDK GitHub) в Docker: сводка по открытым issue
   репозиториев GitHub (постраничная выдача по `Link`, кэш ETag, лимиты запросов, повторные попытки, журнал).
3. **api_tests** — интеграционные тесты службы на Python/pytest: чёрный ящик против любого сервера, совместимого с
   контрактом; набор проверяется мутациями (убиваются 43 из 43 нарушений).

Правила разработки — в [CLAUDE.md](CLAUDE.md). Соответствие ТЗ по пунктам — в [docs/COMPLIANCE.md](docs/COMPLIANCE.md).

## Структура репозитория

| Путь | Назначение |
|------|------------|
| `msgfmt/` | Часть 1: `include/msgfmt`, `src`, `tests`, `CMakeLists.txt`, [README](msgfmt/README.md) |
| `service/` | Часть 2: модуль Go (`cmd/ghreport`, `internal/...`), `Dockerfile`, `.dockerignore`, [README](service/README.md) |
| `api_tests/` | Часть 3: тесты `test_00…test_09`, `helpers/`, `selfcheck/` (stub-сервер, мутанты, мета-тесты), [README](api_tests/README.md) |
| `tools/` | `check_env.py`, `check_contracts.py` (контракты и `covers:`), `check_repo.py` (гигиена), `verify_all.py`, `ghreport-docker.sh`; тесты в `tools/tests/` |
| `docs/` | Спецификация (`spec_*.md`), [decisions.md](docs/decisions.md), [COMPLIANCE.md](docs/COMPLIANCE.md), [WORKLOG.md](docs/WORKLOG.md), [submission_email.md](docs/submission_email.md) |

## Быстрый старт

Проверить окружение: `python tools/check_env.py` (ожидается Linux, C++17-компилятор, CMake ≥ 3.20, Go ≥ 1.22,
Python ≥ 3.10, pytest ≥ 7, Docker ≥ 24, git).

### Часть 1 — msgfmt

```
cmake -S msgfmt -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
ctest --test-dir build
printf 'Привет, **мир**!' | build/msgfmt
printf '2 * 3' | build/msgfmt escape
```

С предупреждениями-как-ошибками и санитайзерами (нужен g++/clang с ASan/UBSan, например Linux):
`-DMSGFMT_WERROR=ON -DMSGFMT_SANITIZE=ON`.

### Часть 2 — ghreport

```
docker build -t ghreport service/
docker run --rm -e GITHUB_TOKEN=... ghreport --repos cli/cli --limit 20 --out -
```

Другой сервер, совместимый с контрактом, — тем же способом:
`docker run --rm -e GITHUB_TOKEN=... -e GITHUB_API_BASE_URL=<адрес сервера> ghreport --repos cli/cli --out -`.
Без Docker: `cd service && go build -o ghreport ./cmd/ghreport && GITHUB_TOKEN=... ./ghreport --repos cli/cli --limit 20 --out -`.
Юнит-тесты: `cd service && gofmt -l . && go vet ./... && go test ./... -race -count=1`.

### Часть 3 — api_tests

```
pip install -r api_tests/requirements.txt
python api_tests/selfcheck/stub_server.py --port 8765          # учебный сервер (в другом терминале)
export GHREPORT_BIN=/абсолютный/путь/к/ghreport
export GITHUB_API_BASE_URL=http://127.0.0.1:8765
pytest api_tests -q
```

Мета-тесты самого набора (сервер и бинарник не нужны): `pytest api_tests -m selfcheck -q`.
Мутационная проверка: `python api_tests/selfcheck/run_mutants.py` (≈ 15 минут).

## Проверка всего сразу: `verify_all`

```
python tools/verify_all.py                  # все шаги
python tools/verify_all.py --with-mutants   # + мутационная проверка набора (≈ 15 минут)
python tools/verify_all.py --only service,suite --keep
```

Скрипт делает `git clone --no-hardlinks` во временный каталог (проверяется **только закоммиченное** состояние) и
выполняет шаги `env`, `msgfmt-release` (Release + `-Werror` + ctest), `msgfmt-sanitize` (ASan/UBSan, ноль сообщений),
`service` (gofmt, vet, `go test -race`, сборка), `contracts` (линтер контрактов для msgfmt, service, api_tests, tools),
`hygiene` (секреты, бинарники, `.gitignore`), `selfchecks`, `suite` (основной набор против stub-сервера и бинарника,
`GHREPORT_STRICT=1`), `docker-image` (сборка, `USER` не root, код 2 при `--repos` без значения), `docker-run` (прогон
из контейнера и `pytest` через [tools/ghreport-docker.sh](tools/ghreport-docker.sh)) и, по флагу, `mutants`. В конце
печатается таблица ok/fail/skip с временем. Код возврата 1, если хотя бы один шаг упал; `skip` всегда с причиной.

Полный прогон, включая санитайзеры и контейнер, рассчитан на Linux-хост с Docker. На Windows санитайзеры (mingw) и
`docker-run` (`--network host` не достаёт до loopback хоста) пропускаются автоматически.

## Затраченное время

| Этап | Что сделано | Время |
|------|-------------|-------|
| 0. Инфраструктура | структура, `check_env`, `check_contracts` | 20 минут |
| 1. msgfmt | каркас, UTF-8, парсер, escape, JSON, CLI, фаззинг | 3 часа |
| 2. ghreport | пакеты, транспорт, кэш, сборка службы, Docker | 4 часов |
| 3. api_tests | инфраструктура, тесты службы, мутации | 2.5 часа |
| 4. Финальная проверка | `verify_all`, `check_repo`, COMPLIANCE, README | TODO: заполнить |
| **Итого** | | TODO: заполнить |

## Использованные ИИ-инструменты

- Claude Code (CLI от Anthropic) — написание кода, тестов и документации по всем этапам.

## Ключевые принятые решения

Полный перечень с обоснованиями и именами тестов — [docs/decisions.md](docs/decisions.md). Главное:

* **Правила ТЗ приоритетнее примеров.** Пример 9: `**жирный _и курсив_**` даёт italic(7,8), а `**жирный _и_ курсив**` —
  italic(7,1); тестируются оба входа.
* **«Буква или цифра»** (правило 3) — переносимая таблица интервалов категорий L* и Nd без `<locale>`/ICU;
  письменности вне списка и комбинирующие знаки не входят.
* **UTF-8 декодер строгий** (overlong, суррогаты, > U+10FFFF — ошибка); коды msgfmt при неверных аргументах: 64, при
  внутренней ошибке: 70 (ТЗ определяет только 0 и 2).
* **Формат JSON:** отчёты компактные; объекты ошибок печатаются ровно как в ТЗ — `{"error": "all_failed"}`.
* **ghreport:** единственный репозиторий с ошибкой — `all_failed` (код 3); `-h` — код 0; строгий разбор `--limit` и
  `--since`; ошибки кэша — предупреждение, служба работает без кэша; токен не уходит на другой origin при редиректе.
* **api_tests:** ожидаемый результат вычисляет оракул (сервер заранее неизвестен); сбои вносит прокси; нехватка данных
  у сервера — `skip` с причиной (в `GHREPORT_STRICT=1` — падение); реальные паузы только в трёх тестах `slow`.
* **Контракты и `covers:`** у каждой функции проверяются `tools/check_contracts.py`.

## Известные ограничения

* Определение «буквы/цифры» приближённое (таблица, а не полная база Unicode), см. [msgfmt/README.md](msgfmt/README.md).
* Санитайзеры на Windows (mingw) недоступны; проверены в Linux (WSL Fedora, g++ 14).
* Полный прогон контейнера против локального сервера (`docker-run`) возможен только на Linux-хосте; на Docker Desktop
  проверены сборка образа, `USER 65532`, код 2, монтирования и аргументы обёртки.
* Набор api_tests не видит того, что не отражается в трафике, выводе и файлах (внутренний формат кэша, реальные
  таймауты 30 с, сигналы); граница `--since` для смещений часовых поясов зависит от данных сервера. Подробнее —
  [api_tests/README.md](api_tests/README.md).
* Первые два коммита истории (`add spec`, `add spec pt. 2`) не в стиле Conventional Commits; историю не переписывали.
* Раздел 6 ТЗ в `docs/spec_*.md` отсутствует и не аудировался.
